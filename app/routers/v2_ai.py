"""
Project Pulse V2 — AI Capture Router (Unified Omnibar Handler)
Handles multimodal input (text + audio + image) through the two-pass AI pipeline,
resolves entities, and writes structured logs to the user's diary/gym profile.

Features:
- Localized meal window heuristic (timezone-aware)
- Rich structured JSON response payloads
- Semantic memory storage
- Conversational history hydration (GET /history)

Prefix: /api/v2/ai
"""

import logging
import uuid
from datetime import datetime, timezone as tz

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from google import genai
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.ai import ChatMessage, Conversation
from app.models.identity import Profile
from app.routers.dependencies import get_current_user
from app.schemas.identity import ProfileResponse
from app.schemas.nutrition import NutritionLogCreate, NutritionLogResponse
from app.schemas.training import WorkoutSetCreate
from app.services.ai import ai_service, memory_service
from app.services.nutrition import nutrition_service
from app.services.training import training_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v2/ai",
    tags=["AI Capture"],
)


@router.get("/history")
async def get_chat_history(
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieves the authenticated user's active conversation history.

    - Finds the user's active (un-pruned) conversation.
    - If none exists, creates a new one.
    - Returns the last 20 messages sorted chronologically (ASC).
    """
    logger.info(f"[API] GET /ai/history — user: {str(current_user.id)[:8]}")

    try:
        # Find active, un-pruned conversation for this user
        stmt = (
            select(Conversation)
            .where(
                Conversation.user_id == current_user.id,
                Conversation.is_active == True,
            )
            .order_by(Conversation.started_at.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        conversation = result.scalar_one_or_none()

        # If no active conversation exists, create one
        if conversation is None:
            conversation = Conversation(
                user_id=current_user.id,
                started_at=datetime.utcnow(),
                is_active=True,
            )
            db.add(conversation)
            await db.flush()
            await db.refresh(conversation)
            logger.info(
                f"[API] Created new conversation {str(conversation.id)[:8]} for user {str(current_user.id)[:8]}"
            )

        # Query the last 20 messages for this conversation, sorted chronologically
        msg_stmt = (
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation.id)
            .order_by(ChatMessage.created_at.asc())
            .limit(20)
        )
        msg_result = await db.execute(msg_stmt)
        messages = msg_result.scalars().all()

        await db.commit()

        return {
            "conversation_id": str(conversation.id),
            "messages": [
                {
                    "id": str(msg.id),
                    "role": msg.role,
                    "content": msg.content,
                    "created_at": msg.created_at.isoformat() if msg.created_at else None,
                }
                for msg in messages
            ],
        }
    except Exception as e:
        await db.rollback()
        logger.error(f"[API] GET /ai/history failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve chat history: {str(e)}",
        )


def _resolve_meal_type_from_hour(hour: int) -> str:
    """
    Determines the meal type based on the user's local hour.
    05:00–10:59 → breakfast
    11:00–15:59 → lunch
    16:00–21:59 → dinner
    Otherwise   → snack
    """
    if 5 <= hour <= 10:
        return "breakfast"
    elif 11 <= hour <= 15:
        return "lunch"
    elif 16 <= hour <= 21:
        return "dinner"
    else:
        return "snack"


def _get_user_local_hour(user_timezone: str) -> int:
    """Returns the current hour in the user's timezone (simplified offset lookup)."""
    from app.services.scheduler import estimate_timezone_offset
    offset = estimate_timezone_offset(user_timezone)
    utc_now = datetime.utcnow()
    local_hour = (utc_now.hour + offset) % 24
    return local_hour


@router.post("/capture")
async def capture_input(
    text: str | None = Form(default=None, description="Text input from the omnibar"),
    file: UploadFile | None = File(default=None, description="Audio or image file upload"),
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Unified Omnibar Capture Endpoint.

    Accepts text, audio, or image input. Processes through the AI pipeline
    and writes structured logs. Returns rich structured JSON payloads.
    """
    logger.info(
        f"[API] POST /ai/capture — user: {str(current_user.id)[:8]}, "
        f"text: {'yes' if text else 'no'}, file: {'yes' if file else 'no'}"
    )

    if not text and not file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either 'text' or 'file' must be provided",
        )

    # Step 0: Resolve the per-request Gemini client up front so transcription
    # (Pass 1) and parsing (Pass 2) share the same BYOK/server-key client.
    try:
        client = await ai_service.resolve_client(db=db, user_id=current_user.id)
    except RuntimeError as e:
        logger.error(f"[CAPTURE] No AI client available: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI service is not configured. Set a BYOK key or contact support.",
        )

    # Step 1: Transcribe audio / process image if provided
    transcript = text
    if file:
        try:
            file_bytes = await file.read()
            mime_type = file.content_type or "audio/webm"

            # If it's an image, treat it as a photo capture (pass directly to parser)
            if mime_type.startswith("image/"):
                transcript = text or "[Photo input]"
            else:
                # Audio transcription
                transcript = await ai_service.transcribe_audio_verbatim(
                    client=client,
                    audio_bytes=file_bytes,
                    mime_type=mime_type,
                )
                if not transcript:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="No speech detected in audio file",
                    )
                if text:
                    transcript = f"{text}. {transcript}"

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"[CAPTURE] Processing failed: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="File processing failed",
            )

    # Step 2: Retrieve semantic memory context (non-blocking, non-fatal)
    try:
        await memory_service.search_semantic_memory(
            db=db, user_id=current_user.id, query_text=transcript, client=client,
        )
    except Exception as e:
        logger.warning(f"[CAPTURE] Memory retrieval failed (non-fatal): {e}")

    # Step 3: Parse text into structured JSON
    try:
        parsed_result = await ai_service.parse_verbatim_text_to_json(client, transcript)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except Exception as e:
        logger.error(f"[CAPTURE] Parsing error: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="AI parsing failed")

    # Step 4: Route to domain handler
    entry_type = parsed_result.get("entry_type", "")

    if entry_type == "nutrition":
        return await _handle_nutrition_entry(
            db=db, user_id=current_user.id, user_timezone=current_user.timezone,
            parsed_result=parsed_result, raw_transcript=transcript, client=client,
        )
    elif entry_type == "workout":
        return await _handle_workout_entry(
            db=db, user_id=current_user.id,
            parsed_result=parsed_result, raw_transcript=transcript, client=client,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unrecognized entry type: '{entry_type}'",
        )


async def _handle_nutrition_entry(
    db: AsyncSession,
    user_id: uuid.UUID,
    user_timezone: str,
    parsed_result: dict,
    raw_transcript: str,
    client: genai.Client,
) -> dict:
    """
    Processes nutrition entry with localized meal heuristic and returns rich payload.
    """
    logger.info(f"[CAPTURE] Processing nutrition entry for user {str(user_id)[:8]}")

    nutrition_data = parsed_result.get("nutrition", parsed_result)
    items = nutrition_data.get("items", [])
    meal_type = nutrition_data.get("meal_type", "")

    # Localized meal window heuristic
    if not meal_type or meal_type == "unknown":
        local_hour = _get_user_local_hour(user_timezone)
        meal_type = _resolve_meal_type_from_hour(local_hour)
        logger.info(f"[CAPTURE] Meal type resolved from timezone: {meal_type} (hour={local_hour})")

    if not items:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No food items found in parsed input",
        )

    logged_items = []

    for item in items:
        serving_g = item.get("serving_size_g", 100)
        ai_macros = {
            "calories_per_100g": item.get("calories", 0) / max(serving_g, 1) * 100,
            "protein_per_100g": item.get("protein_g", 0) / max(serving_g, 1) * 100,
            "carbs_per_100g": item.get("carbs_g", 0) / max(serving_g, 1) * 100,
            "fat_per_100g": item.get("fat_g", 0) / max(serving_g, 1) * 100,
        }

        food = await nutrition_service.resolve_food_item(
            db=db, name=item.get("name", "Unknown Food"), ai_estimated_macros=ai_macros,
        )

        log_data = NutritionLogCreate(
            logged_at=datetime.utcnow(),
            meal_type=meal_type,
            food_id=food.id,
            recipe_id=None,
            serving_size_g=serving_g,
        )

        await nutrition_service.log_nutrition(db=db, user_id=user_id, log_data=log_data)

        logged_items.append({
            "name": food.name,
            "serving_size_g": serving_g,
            "calories": round(float(food.calories_per_100g) * serving_g / 100),
            "protein": round(float(food.protein_per_100g) * serving_g / 100),
            "carbs": round(float(food.carbs_per_100g) * serving_g / 100),
            "fat": round(float(food.fat_per_100g) * serving_g / 100),
        })

    # Store semantic memory (non-fatal)
    try:
        await memory_service.store_memory(
            db=db, user_id=user_id, context_text=f"User logged nutrition: {raw_transcript}",
            client=client,
        )
    except Exception:
        pass

    # Build summary text
    item_names = ", ".join(i["name"] for i in logged_items)
    summary_text = f"Logged {len(logged_items)} item{'s' if len(logged_items) > 1 else ''} to {meal_type.capitalize()}: {item_names}."

    return {
        "status": "success",
        "type": "nutrition",
        "summary_text": summary_text,
        "payload": {
            "meal_type": meal_type,
            "items": logged_items,
            "total_calories": sum(i["calories"] for i in logged_items),
            "total_protein": sum(i["protein"] for i in logged_items),
            "total_carbs": sum(i["carbs"] for i in logged_items),
            "total_fat": sum(i["fat"] for i in logged_items),
        },
    }


async def _handle_workout_entry(
    db: AsyncSession,
    user_id: uuid.UUID,
    parsed_result: dict,
    raw_transcript: str,
    client: genai.Client,
) -> dict:
    """
    Processes workout entry and returns rich structured payload.
    """
    logger.info(f"[CAPTURE] Processing workout entry for user {str(user_id)[:8]}")

    workout_data = parsed_result.get("workout", parsed_result)
    exercises = workout_data.get("exercises", [])
    session_name = workout_data.get("session_name", "AI-Captured Workout")

    if not exercises:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No exercises found in parsed input",
        )

    session = await training_service.start_workout_session(
        db=db, user_id=user_id, template_id=None, name=session_name,
    )

    logged_exercises = []
    total_sets = 0

    for exercise_data in exercises:
        exercise = await training_service.resolve_exercise(
            db=db,
            name=exercise_data.get("exercise_name", "Unknown Exercise"),
            category=exercise_data.get("category", "strength"),
            primary_muscle_group=exercise_data.get("primary_muscle_group"),
        )

        sets = exercise_data.get("sets", [])
        logged_sets = []

        for set_data in sets:
            set_create = WorkoutSetCreate(
                exercise_id=exercise.id,
                set_number=set_data.get("set_number", total_sets + 1),
                weight_kg=set_data.get("weight_kg"),
                reps=set_data.get("reps"),
                rpe=set_data.get("rpe"),
                completed=set_data.get("completed", True),
            )
            await training_service.log_workout_set(db=db, session_id=session.id, set_data=set_create)
            total_sets += 1
            logged_sets.append({
                "set_number": set_data.get("set_number", total_sets),
                "weight_kg": set_data.get("weight_kg"),
                "reps": set_data.get("reps"),
                "rpe": set_data.get("rpe"),
            })

        logged_exercises.append({
            "name": exercise.name,
            "category": exercise.category,
            "sets": logged_sets,
        })

    completed_session = await training_service.complete_workout_session(
        db=db, user_id=user_id, session_id=session.id,
    )

    # Store semantic memory (non-fatal)
    try:
        await memory_service.store_memory(
            db=db, user_id=user_id, context_text=f"User logged workout: {raw_transcript}",
            client=client,
        )
    except Exception:
        pass

    total_volume = completed_session.total_volume_kg if completed_session else 0
    summary_text = (
        f"Logged {session_name}: {len(logged_exercises)} exercise{'s' if len(logged_exercises) > 1 else ''}, "
        f"{total_sets} sets, {total_volume or 0:.0f}kg total volume."
    )

    return {
        "status": "success",
        "type": "training",
        "summary_text": summary_text,
        "payload": {
            "session_name": session_name,
            "exercises": logged_exercises,
            "total_sets": total_sets,
            "total_volume_kg": total_volume,
        },
    }

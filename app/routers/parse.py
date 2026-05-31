"""
Project Pulse — Parse Router
POST /api/v1/parse       — Accept text input, run AI pipeline, persist to DB.
POST /api/v1/parse/audio — Accept raw audio, two-pass transcription + parsing.
POST /api/v1/parse/image — Accept image + optional caption, vision pipeline.
"""

import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from sqlalchemy import text as sql_text

from app.database import get_db
from app.models.entry import Entry
from app.schemas.parse import ParseRequest, ParseResponse
from app.services.gemini import parse_user_input, transcribe_audio, parse_image_input, get_embedding
from app.services.storage import upload_image

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["parse"])

# Mock user_id until Auth is integrated
MOCK_USER_ID = "00000000-0000-0000-0000-000000000001"

MEMORY_DISTANCE_THRESHOLD = 0.28


# =============================================================
# RAG: Vector Similarity Search with Scaling Math
# =============================================================

async def search_user_memory(user_input: str, db: Session) -> str | None:
    """
    Perform a vector similarity search on user_memory.
    Returns an authoritative context string with scaling math if a match is found.
    """
    try:
        embedding = await get_embedding(user_input)
        embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"

        result = db.execute(
            sql_text("""
                SELECT label, content,
                       content_embedding <=> cast(:embedding as vector) AS distance
                FROM user_memory
                WHERE user_id = :user_id
                ORDER BY content_embedding <=> cast(:embedding as vector)
                LIMIT 1
            """),
            {"user_id": MOCK_USER_ID, "embedding": embedding_str},
        ).mappings().fetchone()

        if result:
            distance = float(result["distance"])
            print(f"[Vector Search] Closest: {result['label']}, Distance: {distance:.4f}")
            logger.info(f"[RAG] Closest match: '{result['label']}' | Distance: {distance:.4f} | Threshold: {MEMORY_DISTANCE_THRESHOLD}")

            if distance < MEMORY_DISTANCE_THRESHOLD:
                content = result["content"] if isinstance(result["content"], dict) else json.loads(result["content"])
                content_str = json.dumps(content)
                label = result["label"]

                base_serving = content.get("base_serving", 1)
                base_unit = content.get("base_unit", "serving")
                base_macros = content.get("macros", {})
                base_macros_str = json.dumps(base_macros)

                logger.info(f"[RAG] ✓ MATCH HIT — injecting memory context with scaling math")
                print(f"[RAG Query] User Input: '{user_input}' -> Best Match: '{label}' (Distance: {distance:.4f})")

                return (
                    f"SYSTEM CONTEXT:\n"
                    f"We found a potential custom food match in the user's history:\n"
                    f"- Match Label: '{label}'\n"
                    f"- Base Serving Size: {base_serving} {base_unit}\n"
                    f"- Base Macros: {base_macros_str}\n\n"
                    f"INSTRUCTION:\n"
                    f"Evaluate if the user's active input ('{user_input}') actually refers to this custom match.\n"
                    f"- **If yes** (e.g., they said 'smoothie' and the match is 'Mom's Shake', or 'burrito' and the match is 'Smith's Burrito'): "
                    f"You MUST use the exact name '{label}' and scale the macros based on the quantity requested. "
                    f"multiplier = (User's Requested Quantity) / (Base Serving Size = {base_serving}). "
                    f"If no quantity specified, assume multiplier = 1.\n"
                    f"- **If no** (e.g., they said 'eggs' but the match is 'protein shake', or 'apples' but the match is 'bread'): "
                    f"Ignore this context completely and estimate the nutrition of the user's input ('{user_input}') using your own standard database."
                )
            else:
                logger.info(f"[RAG] ✗ Below threshold — no injection")
        else:
            logger.info(f"[RAG] No memories stored yet")

        return None

    except Exception as e:
        logger.warning(f"[RAG] Memory search failed (non-fatal): {e}")
        db.rollback()
        return None


# =============================================================
# Auto-Save: Unconditionally save all food items to memory
# =============================================================

async def auto_save_food_items(ai_result: dict, db: Session):
    """
    After a successful food parse, unconditionally save every food item
    to user_memory for future RAG retrieval. This ensures the app
    "learns" every food the user logs.
    """
    if ai_result.get("type") != "food":
        return

    parsed_data = ai_result.get("parsed_data", {})
    items = parsed_data.get("items", [])

    for item in items:
        label = item.get("name", "").strip()
        if not label or len(label) < 3:
            continue

        macros = item.get("macros", {"p": 0, "c": 0, "f": 0, "kcal": 0})
        original_weight = item.get("original_weight", 1)
        original_unit = item.get("original_unit", "serving")

        content = {
            "base_serving": original_weight,
            "base_unit": original_unit,
            "macros": macros,
        }

        try:
            print("--------------------------------------------------")
            print(f"[AUTO-SAVE] Custom food detected: '{label}'")
            print(f"[AUTO-SAVE] Generating embedding and saving to SQL user_memory table...")
            print("--------------------------------------------------")

            embedding = await get_embedding(label)
            embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"
            content_json = json.dumps(content)

            db.execute(
                sql_text("""
                    INSERT INTO user_memory (user_id, category, label, content, content_embedding)
                    VALUES (:user_id, 'recipe', :label, cast(:content as jsonb), cast(:embedding as vector))
                    ON CONFLICT (user_id, lower(label))
                    DO UPDATE SET content = cast(:content as jsonb), content_embedding = cast(:embedding as vector), updated_at = NOW()
                """),
                {
                    "user_id": MOCK_USER_ID,
                    "label": label,
                    "content": content_json,
                    "embedding": embedding_str,
                },
            )
            db.commit()

            print(f"[AUTO-SAVE] ✓ Successfully saved '{label}' to user_memory!")
            logger.info(f"[AUTO-SAVE] ✓ Upserted '{label}' -> {content_json}")

        except Exception as e:
            print(f"[AUTO-SAVE] ✗ Failed for '{label}': {e}")
            logger.warning(f"[AUTO-SAVE] Failed for '{label}': {e}")
            db.rollback()


# =============================================================
# Routes
# =============================================================

@router.post("/parse", response_model=ParseResponse)
async def parse_text(request: ParseRequest, db: Session = Depends(get_db)):
    """
    All inputs are LOG_ENTRY. After parsing:
    - If food type, unconditionally auto-save all items to memory.
    - RAG search runs before parsing to inject known items.
    """
    try:
        # RAG: Search user memory for relevant context
        memory_context = await search_user_memory(request.text, db)

        # Parse with optional memory context
        ai_result = await parse_user_input(
            raw_input=request.text,
            user_id=MOCK_USER_ID,
            memory_context=memory_context,
        )

        # Persist entry
        entry = Entry(
            user_id=UUID(MOCK_USER_ID),
            type=ai_result["type"],
            status="pending",
            raw_input=request.text,
            parsed_data=ai_result["parsed_data"],
            confidence_score=ai_result["confidence_score"],
        )

        db.add(entry)
        db.commit()
        db.refresh(entry)

        # Auto-save all food items to memory (unconditional)
        await auto_save_food_items(ai_result, db)

        return ParseResponse(
            id=entry.id,
            user_id=entry.user_id,
            type=entry.type,
            status=entry.status,
            raw_input=entry.raw_input,
            parsed_data=entry.parsed_data,
            confidence_score=entry.confidence_score,
            short_persona_response=ai_result["short_persona_response"],
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Parse pipeline error: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to process input: {str(e)}")


@router.post("/parse/audio", response_model=ParseResponse)
async def parse_audio(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Two-Pass Cloud Audio Pipeline with RAG + auto-save.
    """
    try:
        audio_bytes = await file.read()

        if len(audio_bytes) == 0:
            raise HTTPException(status_code=400, detail="Empty audio file")

        mime_type = file.content_type or "audio/webm"
        logger.info(f"[AUDIO] Received: {len(audio_bytes)} bytes, mime: {mime_type}")

        # Step 1 — Verbatim transcription
        transcript = await transcribe_audio(audio_bytes=audio_bytes, mime_type=mime_type)
        logger.info(f"[AUDIO] Transcript: '{transcript}'")

        if not transcript or not transcript.strip():
            raise HTTPException(status_code=400, detail="No speech detected. Please try again.")

        # RAG: Search user memory
        memory_context = await search_user_memory(transcript, db)

        # Step 2 — Parse with memory context
        ai_result = await parse_user_input(
            raw_input=transcript,
            user_id=MOCK_USER_ID,
            memory_context=memory_context,
        )

        entry = Entry(
            user_id=UUID(MOCK_USER_ID),
            type=ai_result["type"],
            status="pending",
            raw_input=transcript,
            parsed_data=ai_result["parsed_data"],
            confidence_score=ai_result["confidence_score"],
        )

        db.add(entry)
        db.commit()
        db.refresh(entry)

        # Auto-save food items to memory
        await auto_save_food_items(ai_result, db)

        return ParseResponse(
            id=entry.id,
            user_id=entry.user_id,
            type=entry.type,
            status=entry.status,
            raw_input=entry.raw_input,
            parsed_data=entry.parsed_data,
            confidence_score=entry.confidence_score,
            short_persona_response=ai_result["short_persona_response"],
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Audio parse error: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to process audio: {str(e)}")


@router.post("/parse/image", response_model=ParseResponse)
async def parse_image(
    file: UploadFile = File(...),
    caption: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """
    Vision Pipeline with optional caption + auto-save.
    """
    try:
        image_bytes = await file.read()

        if len(image_bytes) == 0:
            raise HTTPException(status_code=400, detail="Empty image file")

        mime_type = file.content_type or "image/jpeg"
        logger.info(f"[IMAGE] Received: {len(image_bytes)} bytes, mime: {mime_type}, caption: '{caption or ''}'")

        # Upload to Supabase Storage
        media_url = await upload_image(
            image_bytes=image_bytes,
            mime_type=mime_type,
            user_id=MOCK_USER_ID,
        )

        # Gemini Vision Analysis
        ai_result = await parse_image_input(
            image_bytes=image_bytes,
            mime_type=mime_type,
            caption=caption,
        )

        # Persist to database
        raw_input = caption if caption else "[Photo input]"
        entry = Entry(
            user_id=UUID(MOCK_USER_ID),
            type=ai_result["type"],
            status="pending",
            raw_input=raw_input,
            media_path=media_url if media_url else None,
            parsed_data=ai_result["parsed_data"],
            confidence_score=ai_result["confidence_score"],
        )

        db.add(entry)
        db.commit()
        db.refresh(entry)

        # Auto-save food items to memory
        await auto_save_food_items(ai_result, db)

        return ParseResponse(
            id=entry.id,
            user_id=entry.user_id,
            type=entry.type,
            status=entry.status,
            raw_input=entry.raw_input,
            parsed_data=entry.parsed_data,
            confidence_score=entry.confidence_score,
            short_persona_response=ai_result["short_persona_response"],
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Image parse error: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to process image: {str(e)}")

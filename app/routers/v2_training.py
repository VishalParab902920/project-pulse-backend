"""
Project Pulse V2 — Training Router
Endpoints for workout session management, set logging, and exercise history.

Prefix: /api/v2/training
"""

import logging
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.training import WorkoutTemplate
from app.routers.dependencies import get_current_user
from app.schemas.identity import ProfileResponse
from app.schemas.training import (
    WorkoutSessionResponse,
    WorkoutSetCreate,
    WorkoutSetResponse,
)
from app.services.training import training_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v2/training",
    tags=["Training"],
)


@router.post(
    "/session/start",
    response_model=WorkoutSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_workout_session(
    name: str = Query(
        default="Workout Session",
        description="Name for the workout session",
    ),
    template_id: uuid.UUID | None = Query(
        default=None,
        description="Optional workout template UUID to link",
    ),
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Starts a new workout session for the authenticated user.

    Optionally links to a workout template. The session begins immediately
    with started_at set to the current UTC timestamp.
    """
    logger.info(
        f"[API] POST /training/session/start — user: {str(current_user.id)[:8]}, "
        f"name: '{name}', template: {template_id}"
    )

    session = await training_service.start_workout_session(
        db=db,
        user_id=current_user.id,
        template_id=template_id,
        name=name,
    )

    return session


@router.post(
    "/set/log",
    response_model=WorkoutSetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def log_workout_set(
    session_id: uuid.UUID = Query(
        ...,
        description="The workout session UUID to add this set to",
    ),
    set_data: WorkoutSetCreate = ...,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Logs an individual workout set to an active session.

    The set includes exercise reference, set number, weight, reps, RPE,
    and completion status. Triggers background database write.
    """
    logger.info(
        f"[API] POST /training/set/log — user: {str(current_user.id)[:8]}, "
        f"session: {str(session_id)[:8]}, set #{set_data.set_number}"
    )

    # Validate the session belongs to the user
    session = await training_service.get_workout_session(
        db=db,
        user_id=current_user.id,
        session_id=session_id,
    )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workout session not found or not owned by user",
        )

    try:
        result = await training_service.log_workout_set(
            db=db,
            session_id=session_id,
            set_data=set_data,
        )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.get(
    "/exercise/{exercise_id}/previous",
    response_model=list[WorkoutSetResponse],
)
async def get_previous_exercise_sets(
    exercise_id: uuid.UUID,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieves the user's most recent sets for a specific exercise.

    Returns all sets from the last completed workout session containing
    this exercise. Critical for progressive-overload visualization —
    shows what the user lifted last time.

    Returns an empty list if no history exists.
    """
    logger.info(
        f"[API] GET /training/exercise/{str(exercise_id)[:8]}/previous — "
        f"user: {str(current_user.id)[:8]}"
    )

    sets = await training_service.get_previous_exercise_sets(
        db=db,
        user_id=current_user.id,
        exercise_id=exercise_id,
    )

    return sets


@router.post(
    "/session/{session_id}/complete",
    response_model=WorkoutSessionResponse,
)
async def complete_workout_session(
    session_id: uuid.UUID,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Completes a workout session and calculates total volume.

    Total volume = sum(weight_kg × reps) for all completed sets.
    Sets completed_at to the current UTC timestamp.

    Returns 404 if the session doesn't exist or isn't owned by the user.
    """
    logger.info(
        f"[API] POST /training/session/{str(session_id)[:8]}/complete — "
        f"user: {str(current_user.id)[:8]}"
    )

    result = await training_service.complete_workout_session(
        db=db,
        user_id=current_user.id,
        session_id=session_id,
    )

    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workout session not found or not owned by user",
        )

    return result


@router.get(
    "/sessions",
    response_model=list[WorkoutSessionResponse],
)
async def get_user_sessions(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    target_date: date | None = Query(default=None, description="Optional date filter (YYYY-MM-DD). Returns only sessions started on this date."),
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieves a paginated list of the user's workout sessions.

    Ordered by most recent first. Includes nested sets with exercise details.
    If target_date is provided, filters to only sessions whose started_at
    falls within that calendar day (00:00:00 to 23:59:59).
    """
    from datetime import datetime as dt, time

    logger.info(
        f"[API] GET /training/sessions — user: {str(current_user.id)[:8]}, "
        f"limit: {limit}, offset: {offset}, target_date: {target_date}"
    )

    if target_date:
        # Date-scoped query: filter sessions started on the target calendar day
        day_start = dt.combine(target_date, time.min)
        day_end = dt.combine(target_date, time.max)

        sessions = await training_service.get_user_sessions(
            db=db,
            user_id=current_user.id,
            limit=limit,
            offset=offset,
            date_start=day_start,
            date_end=day_end,
        )
    else:
        sessions = await training_service.get_user_sessions(
            db=db,
            user_id=current_user.id,
            limit=limit,
            offset=offset,
        )

    return sessions


# =============================================================
# Quick-Log Endpoint (Retrospective Workout Logging)
# =============================================================


class QuickLogSetInput(BaseModel):
    """Single set in a quick-log payload."""
    set_number: int = Field(ge=1)
    weight_kg: float | None = None
    reps: int | None = None
    rpe: float | None = None


class QuickLogExerciseInput(BaseModel):
    """Single exercise with sets in a quick-log payload."""
    exercise_id: uuid.UUID
    sets: list[QuickLogSetInput] = Field(min_length=1)


class QuickLogRequest(BaseModel):
    """Payload for retrospective workout quick-logging."""
    name: str = Field(min_length=1, max_length=255)
    logged_at: str  # ISO datetime string
    exercises: list[QuickLogExerciseInput] = Field(min_length=1)


@router.post("/session/quick-log", status_code=status.HTTP_201_CREATED)
async def quick_log_session(
    payload: QuickLogRequest,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrospective workout quick-log. Creates a completed session with all sets
    written directly — no active timer, no progressive tracking.
    Both started_at and completed_at are set to logged_at.
    """
    from app.models.training import Exercise, WorkoutSession, WorkoutSet

    user_id = current_user.id
    logger.info(f"[API] POST /training/session/quick-log — user: {str(user_id)[:8]}, name: '{payload.name}'")

    from datetime import datetime as dt
    try:
        logged_at = dt.fromisoformat(payload.logged_at.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid 'logged_at' datetime: '{payload.logged_at}'. Expected ISO 8601 format.",
        )

    # Strict exercise-ID validation BEFORE any insert.
    # Resolve the set of distinct incoming exercise_ids in a single query so a
    # missing reference returns a clean 422 instead of a raw FK 500.
    incoming_exercise_ids = {ex.exercise_id for ex in payload.exercises}
    existing_result = await db.execute(
        select(Exercise.id).where(Exercise.id.in_(incoming_exercise_ids))
    )
    existing_exercise_ids = {row[0] for row in existing_result.all()}

    missing_ids = incoming_exercise_ids - existing_exercise_ids
    if missing_ids:
        missing_id = next(iter(missing_ids))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Exercise ID {missing_id} not found in catalog.",
        )

    session_id = uuid.uuid4()
    session = WorkoutSession(
        id=session_id,
        user_id=user_id,
        template_id=None,
        name=payload.name,
        started_at=logged_at,
        completed_at=logged_at,
        total_volume_kg=None,
    )
    db.add(session)

    total_volume = 0.0
    for exercise_input in payload.exercises:
        for set_input in exercise_input.sets:
            ws = WorkoutSet(
                id=uuid.uuid4(),
                session_id=session_id,
                exercise_id=exercise_input.exercise_id,
                set_number=set_input.set_number,
                weight_kg=set_input.weight_kg,
                reps=set_input.reps,
                rpe=set_input.rpe,
                completed=True,
            )
            db.add(ws)
            if set_input.weight_kg and set_input.reps:
                total_volume += set_input.weight_kg * set_input.reps

    session.total_volume_kg = total_volume
    await db.commit()

    return {
        "id": str(session_id),
        "name": payload.name,
        "logged_at": payload.logged_at,
        "exercises_logged": len(payload.exercises),
        "total_volume_kg": round(total_volume, 1),
    }


# =============================================================
# Template Endpoints
# =============================================================


class TemplateExerciseInput(BaseModel):
    """Single exercise in a template creation payload."""
    exercise_id: uuid.UUID
    target_sets: int = Field(ge=1, le=20)
    default_rest_seconds: int = Field(ge=30, le=600)


class TemplateCreateRequest(BaseModel):
    """Payload for creating a workout template."""
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    exercises: list[TemplateExerciseInput] = Field(default_factory=list)


@router.post("/templates", status_code=status.HTTP_201_CREATED)
async def create_template(
    payload: TemplateCreateRequest,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Creates a new workout template for the authenticated user.
    Stores template metadata; exercise configuration is stored as JSON description.
    """
    user_id = current_user.id
    logger.info(f"[API] POST /training/templates — user: {str(user_id)[:8]}, name: '{payload.name}'")

    import json

    # Serialize exercise config into the description field for now
    # (A dedicated template_exercises join table can be added later)
    exercise_config = [
        {
            "exercise_id": str(e.exercise_id),
            "target_sets": e.target_sets,
            "default_rest_seconds": e.default_rest_seconds,
        }
        for e in payload.exercises
    ]

    template = WorkoutTemplate(
        id=uuid.uuid4(),
        user_id=user_id,
        name=payload.name,
        description=json.dumps({
            "text": payload.description or "",
            "exercises": exercise_config,
        }),
    )

    db.add(template)
    await db.commit()
    await db.refresh(template)

    return {
        "id": str(template.id),
        "user_id": str(user_id),
        "name": template.name,
        "description": payload.description,
        "exercises": exercise_config,
        "created_at": template.created_at.isoformat() if template.created_at else None,
    }


@router.get("/templates")
async def list_templates(
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns all workout templates for the authenticated user."""
    import json

    stmt = (
        select(WorkoutTemplate)
        .where(WorkoutTemplate.user_id == current_user.id)
        .order_by(WorkoutTemplate.created_at.desc())
    )
    result = await db.execute(stmt)
    templates = result.scalars().all()

    response = []
    for t in templates:
        exercises = []
        description_text = t.description or ""
        try:
            parsed = json.loads(t.description) if t.description else {}
            description_text = parsed.get("text", "")
            exercises = parsed.get("exercises", [])
        except (json.JSONDecodeError, TypeError):
            pass

        response.append({
            "id": str(t.id),
            "name": t.name,
            "description": description_text,
            "exercises": exercises,
            "created_at": t.created_at.isoformat() if t.created_at else None,
        })

    return response


@router.get("/exercises/search")
async def search_exercises(
    q: str = Query(min_length=2, max_length=100),
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Searches the exercises catalog by name."""
    from app.models.training import Exercise

    stmt = (
        select(Exercise)
        .where(Exercise.name.ilike(f"%{q}%"))
        .limit(20)
    )
    result = await db.execute(stmt)
    exercises = result.scalars().all()

    return [
        {"id": str(e.id), "name": e.name, "category": e.category}
        for e in exercises
    ]

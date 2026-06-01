"""
Project Pulse V2 — Training Service Layer
Orchestrates exercise resolution, workout session management,
set logging, and progressive-overload history retrieval.

Implements:
    - Exercise entity resolution (exact match → dynamic insert)
    - Workout session lifecycle (start, complete, volume calculation)
    - Individual set logging with exercise binding
    - Previous session set retrieval for progressive overload UI
"""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.models.training import (
    Exercise,
    WorkoutSession,
    WorkoutSet,
    WorkoutTemplate,
)
from app.schemas.training import (
    ExerciseResponse,
    WorkoutSessionResponse,
    WorkoutSetCreate,
    WorkoutSetResponse,
)

logger = logging.getLogger(__name__)


class TrainingService:
    """
    Service layer for workout tracking operations.

    Handles exercise resolution, session management, set logging,
    and historical set retrieval for progressive overload visualization.
    """

    async def resolve_exercise(
        self,
        db: AsyncSession,
        name: str,
        category: str = "strength",
        primary_muscle_group: str | None = None,
    ) -> Exercise:
        """
        Resolves an exercise by name from the canonical exercises table.

        Resolution strategy:
            1. Exact case-insensitive match on exercises.name
            2. Partial ILIKE match for close variants
            3. If not found, dynamically inserts with provided or default category

        Args:
            db: Async database session.
            name: The exercise name to resolve (e.g., "Back Squat", "bench press").
            category: Default category if creating new entry (strength/hypertrophy/cardio).
            primary_muscle_group: Optional muscle group for new entries.

        Returns:
            Exercise: The resolved or newly created exercise record.
        """
        logger.info(f"[TRAINING] Resolving exercise: '{name}'")

        # Step 1: Exact case-insensitive match
        stmt = select(Exercise).where(
            func.lower(Exercise.name) == func.lower(name.strip())
        )
        result = await db.execute(stmt)
        exercise = result.scalar_one_or_none()

        if exercise:
            logger.info(f"[TRAINING] Exact match: '{exercise.name}' ({exercise.id})")
            return exercise

        # Step 2: Partial ILIKE match
        fuzzy_stmt = (
            select(Exercise)
            .where(Exercise.name.ilike(f"%{name.strip()}%"))
            .limit(1)
        )
        fuzzy_result = await db.execute(fuzzy_stmt)
        fuzzy_exercise = fuzzy_result.scalar_one_or_none()

        if fuzzy_exercise:
            logger.info(
                f"[TRAINING] Fuzzy match: '{fuzzy_exercise.name}' for query '{name}'"
            )
            return fuzzy_exercise

        # Step 3: Not found — insert dynamically
        new_exercise = Exercise(
            id=uuid.uuid4(),
            name=name.strip().title(),
            category=category,
            primary_muscle_group=primary_muscle_group,
            user_id=None,  # System-level exercise (not user-specific)
        )
        db.add(new_exercise)
        await db.flush()

        logger.info(
            f"[TRAINING] Created new exercise: '{new_exercise.name}' "
            f"(category: {category})"
        )
        return new_exercise

    async def start_workout_session(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        template_id: uuid.UUID | None = None,
        name: str | None = None,
    ) -> WorkoutSessionResponse:
        """
        Registers a new workout session for the user.

        If a template_id is provided, links the session to the template.
        If no name is provided, defaults to "Workout Session".

        Args:
            db: Async database session.
            user_id: The authenticated user's UUID.
            template_id: Optional workout template to link.
            name: Optional session name (e.g., "Leg Day", "Push Day").

        Returns:
            WorkoutSessionResponse: The created session with empty sets list.
        """
        logger.info(
            f"[TRAINING] Starting session for user {str(user_id)[:8]} — "
            f"name: '{name or 'Workout Session'}', template: {template_id}"
        )

        # Validate template exists if provided
        if template_id:
            template_stmt = select(WorkoutTemplate).where(
                and_(
                    WorkoutTemplate.id == template_id,
                    WorkoutTemplate.user_id == user_id,
                )
            )
            template_result = await db.execute(template_stmt)
            template = template_result.scalar_one_or_none()
            if not template:
                logger.warning(
                    f"[TRAINING] Template {template_id} not found for user — ignoring"
                )
                template_id = None

        session = WorkoutSession(
            id=uuid.uuid4(),
            user_id=user_id,
            template_id=template_id,
            name=name or "Workout Session",
            started_at=datetime.now(timezone.utc),
            completed_at=None,
            total_volume_kg=None,
        )

        db.add(session)
        await db.commit()

        # Re-fetch with relationships loaded
        stmt = (
            select(WorkoutSession)
            .options(
                selectinload(WorkoutSession.sets).joinedload(WorkoutSet.exercise)
            )
            .where(WorkoutSession.id == session.id)
        )
        result = await db.execute(stmt)
        loaded_session = result.unique().scalar_one()

        logger.info(f"[TRAINING] Session started: {loaded_session.id}")
        return WorkoutSessionResponse.model_validate(loaded_session)

    async def complete_workout_session(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
    ) -> WorkoutSessionResponse | None:
        """
        Marks a workout session as completed and calculates total volume.

        Total volume = sum(weight_kg * reps) for all completed sets in the session.

        Args:
            db: Async database session.
            user_id: The authenticated user's UUID.
            session_id: The session to complete.

        Returns:
            WorkoutSessionResponse | None: Updated session, or None if not found.
        """
        logger.info(f"[TRAINING] Completing session {session_id}")

        stmt = (
            select(WorkoutSession)
            .options(
                selectinload(WorkoutSession.sets).joinedload(WorkoutSet.exercise)
            )
            .where(
                and_(
                    WorkoutSession.id == session_id,
                    WorkoutSession.user_id == user_id,
                )
            )
        )
        result = await db.execute(stmt)
        session = result.unique().scalar_one_or_none()

        if not session:
            logger.warning(f"[TRAINING] Session {session_id} not found")
            return None

        # Calculate total volume from completed sets
        total_volume = sum(
            (s.weight_kg or 0) * (s.reps or 0)
            for s in session.sets
            if s.completed
        )

        session.completed_at = datetime.now(timezone.utc)
        session.total_volume_kg = total_volume

        await db.commit()
        await db.refresh(session)

        logger.info(
            f"[TRAINING] Session completed: {session_id} — "
            f"volume: {total_volume:.1f} kg"
        )
        return WorkoutSessionResponse.model_validate(session)

    async def log_workout_set(
        self,
        db: AsyncSession,
        session_id: uuid.UUID,
        set_data: WorkoutSetCreate,
    ) -> WorkoutSetResponse:
        """
        Adds an individual performance set to a workout session.

        Validates the session exists, resolves the exercise reference,
        and persists the set record.

        Args:
            db: Async database session.
            session_id: The workout session to add the set to.
            set_data: Validated set creation data.

        Returns:
            WorkoutSetResponse: The created set with nested exercise details.

        Raises:
            ValueError: If the session does not exist.
        """
        logger.info(
            f"[TRAINING] Logging set #{set_data.set_number} for session "
            f"{str(session_id)[:8]} — exercise: {set_data.exercise_id}"
        )

        # Validate session exists
        session_stmt = select(WorkoutSession).where(
            WorkoutSession.id == session_id
        )
        session_result = await db.execute(session_stmt)
        session = session_result.scalar_one_or_none()

        if not session:
            raise ValueError(f"Workout session {session_id} not found")

        # Create the set record
        workout_set = WorkoutSet(
            id=uuid.uuid4(),
            session_id=session_id,
            exercise_id=set_data.exercise_id,
            set_number=set_data.set_number,
            weight_kg=set_data.weight_kg,
            reps=set_data.reps,
            rpe=set_data.rpe,
            completed=set_data.completed,
        )

        db.add(workout_set)
        await db.commit()

        # Re-fetch with exercise relationship loaded
        stmt = (
            select(WorkoutSet)
            .options(joinedload(WorkoutSet.exercise))
            .where(WorkoutSet.id == workout_set.id)
        )
        result = await db.execute(stmt)
        loaded_set = result.unique().scalar_one()

        logger.info(
            f"[TRAINING] Set logged: {loaded_set.id} — "
            f"{loaded_set.weight_kg}kg × {loaded_set.reps} reps"
        )
        return WorkoutSetResponse.model_validate(loaded_set)

    async def get_previous_exercise_sets(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        exercise_id: uuid.UUID,
    ) -> list[WorkoutSetResponse]:
        """
        Retrieves all sets from the user's most recent workout session
        containing the specified exercise.

        Critical for progressive-overload visualization in the gym UI —
        shows what the user lifted last time for this exact exercise.

        Args:
            db: Async database session.
            user_id: The authenticated user's UUID.
            exercise_id: The exercise to look up history for.

        Returns:
            list[WorkoutSetResponse]: Sets from the most recent session,
                                      ordered by set_number. Empty if no history.
        """
        logger.info(
            f"[TRAINING] Fetching previous sets for exercise {str(exercise_id)[:8]} "
            f"(user {str(user_id)[:8]})"
        )

        # Find the most recent session containing this exercise for this user
        # Subquery: get the latest session_id that has a set with this exercise
        latest_session_subquery = (
            select(WorkoutSet.session_id)
            .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
            .where(
                and_(
                    WorkoutSession.user_id == user_id,
                    WorkoutSet.exercise_id == exercise_id,
                    WorkoutSession.completed_at.isnot(None),  # Only completed sessions
                )
            )
            .order_by(WorkoutSession.started_at.desc())
            .limit(1)
            .scalar_subquery()
        )

        # Fetch all sets from that session for this exercise
        stmt = (
            select(WorkoutSet)
            .options(joinedload(WorkoutSet.exercise))
            .where(
                and_(
                    WorkoutSet.session_id == latest_session_subquery,
                    WorkoutSet.exercise_id == exercise_id,
                )
            )
            .order_by(WorkoutSet.set_number.asc())
        )

        result = await db.execute(stmt)
        sets = result.unique().scalars().all()

        if sets:
            logger.info(
                f"[TRAINING] Found {len(sets)} previous sets — "
                f"last session: {sets[0].session_id}"
            )
        else:
            logger.info("[TRAINING] No previous history found for this exercise")

        return [WorkoutSetResponse.model_validate(s) for s in sets]

    async def get_workout_session(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
    ) -> WorkoutSessionResponse | None:
        """
        Retrieves a single workout session with all sets and exercise details.

        Args:
            db: Async database session.
            user_id: The authenticated user's UUID.
            session_id: The session to retrieve.

        Returns:
            WorkoutSessionResponse | None: The session with nested sets, or None.
        """
        stmt = (
            select(WorkoutSession)
            .options(
                selectinload(WorkoutSession.sets).joinedload(WorkoutSet.exercise)
            )
            .where(
                and_(
                    WorkoutSession.id == session_id,
                    WorkoutSession.user_id == user_id,
                )
            )
        )
        result = await db.execute(stmt)
        session = result.unique().scalar_one_or_none()

        if not session:
            return None

        return WorkoutSessionResponse.model_validate(session)

    async def get_user_sessions(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        limit: int = 20,
        offset: int = 0,
        date_start: datetime | None = None,
        date_end: datetime | None = None,
    ) -> list[WorkoutSessionResponse]:
        """
        Retrieves a paginated list of the user's workout sessions.

        Args:
            db: Async database session.
            user_id: The authenticated user's UUID.
            limit: Maximum number of sessions to return.
            offset: Number of sessions to skip.
            date_start: Optional lower bound for started_at filtering.
            date_end: Optional upper bound for started_at filtering.

        Returns:
            list[WorkoutSessionResponse]: Sessions ordered by most recent first.
        """
        from sqlalchemy import and_ as sa_and

        conditions = [WorkoutSession.user_id == user_id]
        if date_start is not None:
            conditions.append(WorkoutSession.started_at >= date_start)
        if date_end is not None:
            conditions.append(WorkoutSession.started_at <= date_end)

        stmt = (
            select(WorkoutSession)
            .options(
                selectinload(WorkoutSession.sets).joinedload(WorkoutSet.exercise)
            )
            .where(sa_and(*conditions))
            .order_by(WorkoutSession.started_at.desc())
            .limit(limit)
            .offset(offset)
        )

        result = await db.execute(stmt)
        sessions = result.unique().scalars().all()

        return [WorkoutSessionResponse.model_validate(s) for s in sessions]


# Module-level singleton for dependency injection
training_service = TrainingService()

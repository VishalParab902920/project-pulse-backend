"""
Project Pulse V2 — Training Pydantic Schemas
DTOs for: exercises, workout_templates, workout_sessions, workout_sets
"""

import uuid
from datetime import datetime

from pydantic import Field

from app.schemas.base import BaseSchema


# =============================================================
# Exercise Schemas
# =============================================================


class ExerciseBase(BaseSchema):
    """Shared exercise fields."""

    name: str = Field(max_length=255)
    category: str = Field(max_length=100)
    primary_muscle_group: str | None = Field(default=None, max_length=100)


class ExerciseCreate(ExerciseBase):
    """Schema for creating an exercise."""

    pass


class ExerciseUpdate(BaseSchema):
    """Schema for updating an exercise."""

    name: str | None = Field(default=None, max_length=255)
    category: str | None = Field(default=None, max_length=100)
    primary_muscle_group: str | None = Field(default=None, max_length=100)


class ExerciseResponse(ExerciseBase):
    """Public exercise response."""

    id: uuid.UUID
    user_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


# =============================================================
# Workout Template Schemas
# =============================================================


class WorkoutTemplateBase(BaseSchema):
    """Shared workout template fields."""

    name: str = Field(max_length=255)
    description: str | None = None


class WorkoutTemplateCreate(WorkoutTemplateBase):
    """Schema for creating a workout template."""

    pass


class WorkoutTemplateUpdate(BaseSchema):
    """Schema for updating a workout template."""

    name: str | None = Field(default=None, max_length=255)
    description: str | None = None


class WorkoutTemplateResponse(WorkoutTemplateBase):
    """Public workout template response."""

    id: uuid.UUID
    user_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


# =============================================================
# Workout Set Schemas
# =============================================================


class WorkoutSetBase(BaseSchema):
    """Shared workout set fields."""

    exercise_id: uuid.UUID
    set_number: int
    weight_kg: float | None = None
    reps: int | None = None
    rpe: float | None = None
    completed: bool = True


class WorkoutSetCreate(WorkoutSetBase):
    """Schema for creating a workout set."""

    pass


class WorkoutSetUpdate(BaseSchema):
    """Schema for updating a workout set."""

    weight_kg: float | None = None
    reps: int | None = None
    rpe: float | None = None
    completed: bool | None = None


class WorkoutSetResponse(WorkoutSetBase):
    """Public workout set response with nested exercise details."""

    id: uuid.UUID
    session_id: uuid.UUID
    exercise: ExerciseResponse | None = None
    created_at: datetime
    updated_at: datetime


# =============================================================
# Workout Session Schemas
# =============================================================


class WorkoutSessionBase(BaseSchema):
    """Shared workout session fields."""

    name: str | None = Field(default=None, max_length=255)
    template_id: uuid.UUID | None = None
    started_at: datetime


class WorkoutSessionCreate(WorkoutSessionBase):
    """Schema for creating a workout session."""

    sets: list[WorkoutSetCreate] = Field(default_factory=list)
    rating: int | None = None


class WorkoutSessionUpdate(BaseSchema):
    """Schema for updating a workout session."""

    name: str | None = Field(default=None, max_length=255)
    completed_at: datetime | None = None
    total_volume_kg: float | None = None
    rating: int | None = None


class WorkoutSessionResponse(WorkoutSessionBase):
    """Public workout session response with nested sets."""

    id: uuid.UUID
    user_id: uuid.UUID | None = None
    template_id: uuid.UUID | None = None
    completed_at: datetime | None = None
    total_volume_kg: float | None = None
    rating: int | None = None
    sets: list[WorkoutSetResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

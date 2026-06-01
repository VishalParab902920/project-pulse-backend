"""
Project Pulse V2 — Domain 3: Training Models
Maps: exercises, workout_templates, workout_sessions, workout_sets
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Exercise(Base):
    """
    Maps to `exercises` table.
    Canonical list of lifting and cardiovascular movements.
    """

    __tablename__ = "exercises"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    primary_muscle_group: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )

    # Relationships
    workout_sets: Mapped[list["WorkoutSet"]] = relationship(
        "WorkoutSet", back_populates="exercise", lazy="noload"
    )


class WorkoutTemplate(Base):
    """
    Maps to `workout_templates` table.
    Grouping for routine templates created by users.
    """

    __tablename__ = "workout_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )

    # Relationships
    sessions: Mapped[list["WorkoutSession"]] = relationship(
        "WorkoutSession", back_populates="template", lazy="noload"
    )


class WorkoutSession(Base):
    """
    Maps to `workout_sessions` table.
    Tracks gym session executions with optional template linkage.
    """

    __tablename__ = "workout_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=True,
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workout_templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    total_volume_kg: Mapped[float | None] = mapped_column(
        Numeric(8, 2), nullable=True
    )
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )

    # Relationships
    profile: Mapped["Profile"] = relationship(
        "Profile", back_populates="workout_sessions"
    )
    template: Mapped["WorkoutTemplate | None"] = relationship(
        "WorkoutTemplate", back_populates="sessions"
    )
    sets: Mapped[list["WorkoutSet"]] = relationship(
        "WorkoutSet", back_populates="session", lazy="selectin", cascade="all, delete-orphan"
    )


class WorkoutSet(Base):
    """
    Maps to `workout_sets` table.
    Individual exercise set details within a workout session.
    """

    __tablename__ = "workout_sets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workout_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    exercise_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("exercises.id", ondelete="CASCADE"),
        nullable=False,
    )
    set_number: Mapped[int] = mapped_column(Integer, nullable=False)
    weight_kg: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    reps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rpe: Mapped[float | None] = mapped_column(Numeric(3, 1), nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )

    # Relationships
    session: Mapped["WorkoutSession"] = relationship(
        "WorkoutSession", back_populates="sets"
    )
    exercise: Mapped["Exercise"] = relationship(
        "Exercise", back_populates="workout_sets"
    )


# Forward reference imports for type checking
from app.models.identity import Profile  # noqa: E402, F401

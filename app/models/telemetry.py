"""
Project Pulse V2 — Domain 4: Time-Series Telemetry Models
Maps: health_metrics (range-partitioned), daily_health_summaries
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class HealthMetric(Base):
    """
    Maps to `health_metrics` table.
    High-frequency partitioned parent table (PARTITION BY RANGE on timestamp).
    Compound PK: (id, timestamp) to satisfy PostgreSQL partitioning constraints.
    """

    __tablename__ = "health_metrics"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    timestamp: Mapped[datetime] = mapped_column(primary_key=True, nullable=False)
    metric_type: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)

    __table_args__ = (
        Index(
            "idx_health_metrics_query",
            "user_id",
            "metric_type",
            timestamp.desc(),
        ),
        {"implicit_returning": False},  # Required for partitioned tables with asyncpg
    )


class DailyHealthSummary(Base):
    """
    Maps to `daily_health_summaries` table.
    Permanent aggregate table preserving daily health trends.
    Compound PK: (user_id, date).
    """

    __tablename__ = "daily_health_summaries"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    resting_heart_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_calories_burned: Mapped[float | None] = mapped_column(
        Numeric(8, 2), nullable=True
    )
    total_daily_steps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleep_duration_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )

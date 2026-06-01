"""
Project Pulse V2 — Telemetry Pydantic Schemas
DTOs for: health_metrics, daily_health_summaries
"""

import uuid
from datetime import date, datetime

from pydantic import Field

from app.schemas.base import BaseSchema


# =============================================================
# Health Metric Schemas
# =============================================================


class HealthMetricBase(BaseSchema):
    """Shared health metric fields."""

    timestamp: datetime
    metric_type: str = Field(max_length=100)
    value: float


class HealthMetricCreate(HealthMetricBase):
    """Schema for creating a health metric entry."""

    pass


class HealthMetricBatchCreate(BaseSchema):
    """Schema for batch-creating health metrics (wearable sync)."""

    metrics: list[HealthMetricCreate] = Field(min_length=1)


class HealthMetricResponse(HealthMetricBase):
    """Public health metric response."""

    id: uuid.UUID
    user_id: uuid.UUID


# =============================================================
# Daily Health Summary Schemas
# =============================================================


class DailyHealthSummaryBase(BaseSchema):
    """Shared daily health summary fields."""

    date: date
    resting_heart_rate: int | None = None
    active_calories_burned: float | None = None
    total_daily_steps: int | None = None
    sleep_duration_seconds: int | None = None


class DailyHealthSummaryResponse(DailyHealthSummaryBase):
    """Public daily health summary response."""

    user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

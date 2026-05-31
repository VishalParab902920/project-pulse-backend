"""
Project Pulse — Pydantic Schemas for Health Sync
"""

from datetime import datetime

from pydantic import BaseModel, Field


class HealthSyncRequest(BaseModel):
    """Incoming health data from native device or browser simulation."""
    steps: int | None = Field(None, ge=0, description="Step count")
    heart_rate_avg: int | None = Field(None, ge=0, description="Average heart rate in bpm")
    sleep_minutes: int | None = Field(None, ge=0, description="Sleep duration in minutes")
    occurred_at: datetime = Field(..., description="Timestamp of the measurement")


class HealthSyncResponse(BaseModel):
    """Response after syncing health data."""
    status: str
    rows_inserted: int
    metrics_synced: list[str]

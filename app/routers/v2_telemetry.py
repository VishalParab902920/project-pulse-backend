"""
Project Pulse V2 — Telemetry Router
Endpoints for wearable health metric synchronization and daily summaries.

Prefix: /api/v2/telemetry
"""

import logging
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.telemetry import DailyHealthSummary, HealthMetric
from app.routers.dependencies import get_current_user
from app.schemas.identity import ProfileResponse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v2/telemetry",
    tags=["Telemetry"],
)


class MetricPayload(BaseModel):
    """Single health metric entry in a bulk sync payload."""

    timestamp: datetime
    metric_type: str = Field(max_length=100)
    value: float


class BulkSyncRequest(BaseModel):
    """Bulk telemetry sync payload from wearable devices."""

    metrics: list[MetricPayload] = Field(min_length=1, max_length=5000)


class BulkSyncResponse(BaseModel):
    """Response after successful bulk sync."""

    synced_count: int
    summary_updated: bool


@router.post("/sync", response_model=BulkSyncResponse, status_code=status.HTTP_201_CREATED)
async def sync_telemetry(
    payload: BulkSyncRequest,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Bulk-syncs wearable health telemetry data.

    Accepts an array of metric entries (steps, heart_rate, sleep_seconds, etc.)
    and inserts them in a single fast SQL transaction. Automatically updates
    the user's daily_health_summaries for affected dates.

    Designed for batch uploads from Apple HealthKit, Google Fit, Garmin, etc.
    """
    logger.info(
        f"[TELEMETRY] Sync request from user {str(current_user.id)[:8]} — "
        f"{len(payload.metrics)} metrics"
    )

    user_id = current_user.id

    # Build bulk insert records
    records = [
        {
            "id": uuid.uuid4(),
            "user_id": user_id,
            "timestamp": m.timestamp,
            "metric_type": m.metric_type,
            "value": m.value,
        }
        for m in payload.metrics
    ]

    try:
        # Bulk insert using SQLAlchemy core for maximum performance
        from sqlalchemy import insert

        stmt = insert(HealthMetric).values(records)
        await db.execute(stmt)
        await db.commit()

        logger.info(f"[TELEMETRY] Inserted {len(records)} metrics")
    except Exception as e:
        await db.rollback()
        logger.error(f"[TELEMETRY] Bulk insert failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to sync telemetry data",
        )

    # Aggregate and update daily_health_summaries for affected dates
    summary_updated = False
    try:
        affected_dates: set[date] = set()
        for m in payload.metrics:
            affected_dates.add(m.timestamp.date())

        for target_date in affected_dates:
            await _update_daily_health_summary(db, user_id, target_date)

        await db.commit()
        summary_updated = True
        logger.info(
            f"[TELEMETRY] Updated summaries for {len(affected_dates)} dates"
        )
    except Exception as e:
        await db.rollback()
        logger.warning(f"[TELEMETRY] Summary update failed (non-fatal): {e}")

    return BulkSyncResponse(
        synced_count=len(records),
        summary_updated=summary_updated,
    )


@router.get("/summary/{target_date}")
async def get_daily_summary(
    target_date: date,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieves the daily health summary for a specific date.
    """
    stmt = select(DailyHealthSummary).where(
        and_(
            DailyHealthSummary.user_id == current_user.id,
            DailyHealthSummary.date == target_date,
        )
    )
    result = await db.execute(stmt)
    summary = result.scalar_one_or_none()

    if not summary:
        return {
            "user_id": str(current_user.id),
            "date": target_date.isoformat(),
            "resting_heart_rate": None,
            "active_calories_burned": None,
            "total_daily_steps": None,
            "sleep_duration_seconds": None,
        }

    return {
        "user_id": str(summary.user_id),
        "date": summary.date.isoformat(),
        "resting_heart_rate": summary.resting_heart_rate,
        "active_calories_burned": float(summary.active_calories_burned) if summary.active_calories_burned else None,
        "total_daily_steps": summary.total_daily_steps,
        "sleep_duration_seconds": summary.sleep_duration_seconds,
    }


@router.get("/metrics")
async def get_metrics_range(
    metric_type: str,
    start_date: date,
    end_date: date,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieves raw health metrics for a date range and metric type.
    Used for charting detailed time-series data.
    """
    from sqlalchemy import func

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.max.time())

    stmt = (
        select(HealthMetric)
        .where(
            and_(
                HealthMetric.user_id == current_user.id,
                HealthMetric.metric_type == metric_type,
                HealthMetric.timestamp >= start_dt,
                HealthMetric.timestamp <= end_dt,
            )
        )
        .order_by(HealthMetric.timestamp.asc())
        .limit(2000)
    )

    result = await db.execute(stmt)
    metrics = result.scalars().all()

    return [
        {
            "timestamp": m.timestamp.isoformat(),
            "value": float(m.value),
        }
        for m in metrics
    ]


async def _update_daily_health_summary(
    db: AsyncSession,
    user_id: uuid.UUID,
    target_date: date,
) -> None:
    """
    Aggregates health metrics for a specific date and upserts into daily_health_summaries.
    """
    from sqlalchemy import func

    start_dt = datetime.combine(target_date, datetime.min.time())
    end_dt = datetime.combine(target_date, datetime.max.time())

    # Aggregate steps
    steps_stmt = select(func.sum(HealthMetric.value)).where(
        and_(
            HealthMetric.user_id == user_id,
            HealthMetric.metric_type == "steps",
            HealthMetric.timestamp >= start_dt,
            HealthMetric.timestamp <= end_dt,
        )
    )
    steps_result = await db.execute(steps_stmt)
    total_steps = steps_result.scalar()

    # Aggregate resting heart rate (average)
    hr_stmt = select(func.avg(HealthMetric.value)).where(
        and_(
            HealthMetric.user_id == user_id,
            HealthMetric.metric_type == "heart_rate",
            HealthMetric.timestamp >= start_dt,
            HealthMetric.timestamp <= end_dt,
        )
    )
    hr_result = await db.execute(hr_stmt)
    avg_hr = hr_result.scalar()

    # Aggregate sleep duration (sum)
    sleep_stmt = select(func.sum(HealthMetric.value)).where(
        and_(
            HealthMetric.user_id == user_id,
            HealthMetric.metric_type == "sleep_seconds",
            HealthMetric.timestamp >= start_dt,
            HealthMetric.timestamp <= end_dt,
        )
    )
    sleep_result = await db.execute(sleep_stmt)
    total_sleep = sleep_result.scalar()

    # Aggregate active calories
    cal_stmt = select(func.sum(HealthMetric.value)).where(
        and_(
            HealthMetric.user_id == user_id,
            HealthMetric.metric_type == "active_calories",
            HealthMetric.timestamp >= start_dt,
            HealthMetric.timestamp <= end_dt,
        )
    )
    cal_result = await db.execute(cal_stmt)
    total_calories = cal_result.scalar()

    # Upsert
    existing_stmt = select(DailyHealthSummary).where(
        and_(
            DailyHealthSummary.user_id == user_id,
            DailyHealthSummary.date == target_date,
        )
    )
    existing_result = await db.execute(existing_stmt)
    summary = existing_result.scalar_one_or_none()

    if summary:
        summary.total_daily_steps = int(total_steps) if total_steps else summary.total_daily_steps
        summary.resting_heart_rate = int(avg_hr) if avg_hr else summary.resting_heart_rate
        summary.sleep_duration_seconds = int(total_sleep) if total_sleep else summary.sleep_duration_seconds
        summary.active_calories_burned = float(total_calories) if total_calories else summary.active_calories_burned
    else:
        new_summary = DailyHealthSummary(
            user_id=user_id,
            date=target_date,
            total_daily_steps=int(total_steps) if total_steps else None,
            resting_heart_rate=int(avg_hr) if avg_hr else None,
            sleep_duration_seconds=int(total_sleep) if total_sleep else None,
            active_calories_burned=float(total_calories) if total_calories else None,
        )
        db.add(new_summary)

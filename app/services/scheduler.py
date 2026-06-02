"""
Project Pulse V2 — Timezone-Aware Daily Aggregation Scheduler

Runs as a persistent async background loop that:
1. Every hour, determines which timezones are currently crossing midnight.
2. Fetches all users in those timezones.
3. Aggregates their previous 2 days of health metrics and nutrition logs
   into daily_health_summaries and daily_nutrition_summaries.

This ensures late-synced wearable data and backdated logs are captured
without requiring users to manually trigger aggregation.

Efficiency:
- Only processes users whose timezone just crossed midnight (not all users every hour).
- Uses indexed queries on (user_id, timestamp) and (user_id, logged_at).
- Aggregates from raw metrics, not heavy time-series scans.
"""

import asyncio
import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.identity import Profile
from app.models.nutrition import DailyNutritionSummary, NutritionLog
from app.models.telemetry import DailyHealthSummary, HealthMetric

logger = logging.getLogger(__name__)

# All IANA timezone offsets that could represent "midnight right now"
# We check hourly, so we look for timezones where the current local hour is 0.
TIMEZONE_OFFSETS_HOURS = list(range(-12, 15))  # UTC-12 to UTC+14


def get_timezones_at_midnight_now() -> list[str]:
    """
    Returns a list of timezone offset strings (e.g., 'UTC', 'Asia/Kolkata')
    where the local time is currently between 00:00 and 00:59.

    For simplicity, we return the UTC offset range that corresponds to midnight.
    The actual user timezone names are stored in profiles.timezone and compared
    by querying users whose local time is in the midnight hour.
    """
    now_utc = datetime.now(timezone.utc)
    midnight_offsets: list[int] = []

    for offset_hours in TIMEZONE_OFFSETS_HOURS:
        local_hour = (now_utc.hour + offset_hours) % 24
        if local_hour == 0:
            midnight_offsets.append(offset_hours)

    return [f"{h:+d}" for h in midnight_offsets]


async def aggregate_user_daily_health(
    db: AsyncSession,
    user_id,
    target_date: date,
) -> None:
    """
    Aggregates health_metrics for a single user on a single date
    and upserts into daily_health_summaries.
    """
    day_start = datetime.combine(target_date, time.min)
    day_end = datetime.combine(target_date, time.max)

    # Steps (sum)
    steps_result = await db.execute(
        select(func.sum(HealthMetric.value)).where(
            and_(
                HealthMetric.user_id == user_id,
                HealthMetric.metric_type == "steps",
                HealthMetric.timestamp >= day_start,
                HealthMetric.timestamp <= day_end,
            )
        )
    )
    total_steps = steps_result.scalar()

    # Heart rate (average)
    hr_result = await db.execute(
        select(func.avg(HealthMetric.value)).where(
            and_(
                HealthMetric.user_id == user_id,
                HealthMetric.metric_type == "heart_rate",
                HealthMetric.timestamp >= day_start,
                HealthMetric.timestamp <= day_end,
            )
        )
    )
    avg_hr = hr_result.scalar()

    # Sleep (sum)
    sleep_result = await db.execute(
        select(func.sum(HealthMetric.value)).where(
            and_(
                HealthMetric.user_id == user_id,
                HealthMetric.metric_type == "sleep_seconds",
                HealthMetric.timestamp >= day_start,
                HealthMetric.timestamp <= day_end,
            )
        )
    )
    total_sleep = sleep_result.scalar()

    # Active calories (sum)
    cal_result = await db.execute(
        select(func.sum(HealthMetric.value)).where(
            and_(
                HealthMetric.user_id == user_id,
                HealthMetric.metric_type == "active_calories",
                HealthMetric.timestamp >= day_start,
                HealthMetric.timestamp <= day_end,
            )
        )
    )
    total_calories = cal_result.scalar()

    # Skip if no data at all
    if not any([total_steps, avg_hr, total_sleep, total_calories]):
        return

    # Upsert
    existing = await db.execute(
        select(DailyHealthSummary).where(
            and_(
                DailyHealthSummary.user_id == user_id,
                DailyHealthSummary.date == target_date,
            )
        )
    )
    summary = existing.scalar_one_or_none()

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


async def aggregate_user_daily_nutrition(
    db: AsyncSession,
    user_id,
    target_date: date,
) -> None:
    """
    Aggregates nutrition_logs for a single user on a single date
    and upserts into daily_nutrition_summaries.
    """
    day_start = datetime.combine(target_date, time.min)
    day_end = datetime.combine(target_date, time.max)

    # Aggregate macros from nutrition_logs_v2 (pre-calculated fields)
    agg_result = await db.execute(
        select(
            func.sum(NutritionLog.calculated_calories).label("cal"),
            func.sum(NutritionLog.calculated_protein).label("pro"),
            func.sum(NutritionLog.calculated_carbs).label("carb"),
            func.sum(NutritionLog.calculated_fat).label("fat"),
        )
        .select_from(NutritionLog)
        .where(
            and_(
                NutritionLog.user_id == user_id,
                NutritionLog.logged_at >= day_start,
                NutritionLog.logged_at <= day_end,
            )
        )
    )
    row = agg_result.one_or_none()

    if not row or row.cal is None:
        return

    total_cal = float(row.cal) if row.cal else 0
    total_pro = float(row.pro) if row.pro else 0
    total_carb = float(row.carb) if row.carb else 0
    total_fat = float(row.fat) if row.fat else 0

    # Upsert
    existing = await db.execute(
        select(DailyNutritionSummary).where(
            and_(
                DailyNutritionSummary.user_id == user_id,
                DailyNutritionSummary.date == target_date,
            )
        )
    )
    summary = existing.scalar_one_or_none()

    if summary:
        summary.total_calories = total_cal
        summary.total_protein = total_pro
        summary.total_carbs = total_carb
        summary.total_fat = total_fat
    else:
        new_summary = DailyNutritionSummary(
            user_id=user_id,
            date=target_date,
            total_calories=total_cal,
            total_protein=total_pro,
            total_carbs=total_carb,
            total_fat=total_fat,
            total_water_ml=None,
        )
        db.add(new_summary)


async def run_aggregation_for_timezone_users(target_hour_offset: int) -> None:
    """
    Finds all users whose timezone offset matches the given hour offset
    and aggregates their previous 2 days of data.
    """
    logger.info(f"[SCHEDULER] Running aggregation for timezone offset UTC{target_hour_offset:+d}")

    async with async_session() as db:
        try:
            # Fetch users whose timezone is currently at midnight.
            # We use a simplified approach: query all users and filter by timezone.
            # For production scale, this would use a timezone-to-offset lookup table.
            all_profiles_stmt = select(Profile.id, Profile.timezone)
            result = await db.execute(all_profiles_stmt)
            profiles = result.all()

            now_utc = datetime.now(timezone.utc)
            target_users = []

            for profile_id, tz_name in profiles:
                # Simple offset calculation from common timezone names
                user_offset = estimate_timezone_offset(tz_name)
                if user_offset == target_hour_offset:
                    target_users.append(profile_id)

            if not target_users:
                return

            logger.info(
                f"[SCHEDULER] Aggregating for {len(target_users)} users "
                f"in UTC{target_hour_offset:+d} timezone"
            )

            # Aggregate previous 2 days for each user
            today_local = (now_utc + timedelta(hours=target_hour_offset)).date()
            dates_to_aggregate = [today_local - timedelta(days=1), today_local - timedelta(days=2)]

            for user_id in target_users:
                for target_date in dates_to_aggregate:
                    await aggregate_user_daily_health(db, user_id, target_date)
                    await aggregate_user_daily_nutrition(db, user_id, target_date)

            await db.commit()
            logger.info(
                f"[SCHEDULER] Aggregation complete for {len(target_users)} users, "
                f"{len(dates_to_aggregate)} days each"
            )

        except Exception as e:
            await db.rollback()
            logger.error(f"[SCHEDULER] Aggregation failed: {e}")


def estimate_timezone_offset(tz_name: str) -> int:
    """
    Estimates the UTC offset (in hours) for a given timezone name.
    Uses a lookup table for common timezones. Falls back to 0 (UTC).
    """
    TIMEZONE_OFFSETS = {
        "UTC": 0,
        "GMT": 0,
        "America/New_York": -5,
        "America/Chicago": -6,
        "America/Denver": -7,
        "America/Los_Angeles": -8,
        "America/Anchorage": -9,
        "Pacific/Honolulu": -10,
        "America/Sao_Paulo": -3,
        "America/Argentina/Buenos_Aires": -3,
        "Europe/London": 0,
        "Europe/Paris": 1,
        "Europe/Berlin": 1,
        "Europe/Moscow": 3,
        "Africa/Cairo": 2,
        "Africa/Johannesburg": 2,
        "Asia/Dubai": 4,
        "Asia/Karachi": 5,
        "Asia/Kolkata": 5,
        "Asia/Calcutta": 5,
        "Asia/Dhaka": 6,
        "Asia/Bangkok": 7,
        "Asia/Singapore": 8,
        "Asia/Shanghai": 8,
        "Asia/Hong_Kong": 8,
        "Asia/Tokyo": 9,
        "Asia/Seoul": 9,
        "Australia/Sydney": 11,
        "Australia/Melbourne": 11,
        "Australia/Perth": 8,
        "Pacific/Auckland": 12,
    }
    return TIMEZONE_OFFSETS.get(tz_name, 0)


async def start_hourly_timezone_scheduler() -> None:
    """
    Persistent async background loop that runs once per hour.
    Determines which timezones are crossing midnight and triggers
    daily aggregation for users in those timezones.
    """
    logger.info("[SCHEDULER] Hourly timezone aggregation scheduler started")

    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            current_utc_hour = now_utc.hour

            # Find which UTC offset makes it midnight right now
            # If UTC hour is H, then timezone offset O where (H + O) % 24 == 0
            # means that timezone is at midnight.
            for offset in TIMEZONE_OFFSETS_HOURS:
                local_hour = (current_utc_hour + offset) % 24
                if local_hour == 0:
                    await run_aggregation_for_timezone_users(offset)

        except Exception as e:
            logger.error(f"[SCHEDULER] Hourly tick failed: {e}")

        # Sleep for 1 hour
        await asyncio.sleep(3600)

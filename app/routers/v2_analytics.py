"""
Project Pulse V2 — Daily Coaching Analytics Router
Provides AI-generated daily coaching summaries.

Prefix: /api/v2/analytics
"""

import asyncio
import logging
import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.nutrition import DailyNutritionSummary
from app.models.telemetry import DailyHealthSummary
from app.models.training import WorkoutSession
from app.routers.dependencies import get_current_user
from app.schemas.identity import ProfileResponse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v2/analytics",
    tags=["Analytics"],
)

# Simple in-memory cache: key = "user_id:date" → coaching markdown
_coaching_cache: dict[str, str] = {}


@router.get("/daily-coaching")
async def get_daily_coaching(
    target_date: date = Query(default=None, description="Target date (YYYY-MM-DD). Defaults to today."),
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns an AI-generated daily coaching message based on today's
    nutrition, health, and training data.

    Uses a simple in-memory cache keyed by user_id:date to avoid
    regenerating on every request.
    """
    user_id = current_user.id
    coaching_date = target_date or date.today()
    cache_key = f"{user_id}:{coaching_date.isoformat()}"

    # Check cache
    if cache_key in _coaching_cache:
        return {
            "status": "ready",
            "date": coaching_date.isoformat(),
            "coaching_markdown": _coaching_cache[cache_key],
        }

    # Gather today's data
    metrics_summary = await _gather_daily_metrics(db, user_id, coaching_date)

    # Generate coaching via Gemini
    from app.services.ai import ai_service

    try:
        client = await ai_service.resolve_client(db=db, user_id=user_id)
    except RuntimeError:
        client = None

    coaching_md = await _generate_coaching(client, metrics_summary, coaching_date)

    # Cache the result
    _coaching_cache[cache_key] = coaching_md

    return {
        "status": "ready",
        "date": coaching_date.isoformat(),
        "coaching_markdown": coaching_md,
    }


async def _gather_daily_metrics(
    db: AsyncSession,
    user_id: uuid.UUID,
    target_date: date,
) -> str:
    """Gathers nutrition, health, and training data for the target date."""

    lines = [f"## Daily Metrics for {target_date.isoformat()}", ""]

    # Nutrition summary
    nutrition_stmt = (
        select(DailyNutritionSummary)
        .where(
            and_(
                DailyNutritionSummary.user_id == user_id,
                DailyNutritionSummary.date == target_date,
            )
        )
    )
    nutrition_result = await db.execute(nutrition_stmt)
    nutrition = nutrition_result.scalar_one_or_none()

    lines.append("### Nutrition:")
    if nutrition:
        lines.append(f"- Calories: {float(nutrition.total_calories or 0):.0f} kcal")
        lines.append(f"- Protein: {float(nutrition.total_protein or 0):.0f}g")
        lines.append(f"- Carbs: {float(nutrition.total_carbs or 0):.0f}g")
        lines.append(f"- Fat: {float(nutrition.total_fat or 0):.0f}g")
    else:
        lines.append("- No nutrition data logged yet today.")

    # Health summary
    health_stmt = (
        select(DailyHealthSummary)
        .where(
            and_(
                DailyHealthSummary.user_id == user_id,
                DailyHealthSummary.date == target_date,
            )
        )
    )
    health_result = await db.execute(health_stmt)
    health = health_result.scalar_one_or_none()

    lines.append("")
    lines.append("### Health:")
    if health:
        if health.total_daily_steps:
            lines.append(f"- Steps: {health.total_daily_steps:,}")
        if health.active_calories_burned:
            lines.append(f"- Active calories burned: {health.active_calories_burned}")
        if health.resting_heart_rate:
            lines.append(f"- Resting heart rate: {health.resting_heart_rate} bpm")
        if health.sleep_duration_seconds:
            sleep_hrs = health.sleep_duration_seconds / 3600
            lines.append(f"- Sleep: {sleep_hrs:.1f} hours")
    else:
        lines.append("- No health data synced today.")

    # Training sessions
    start_dt = datetime.combine(target_date, datetime.min.time())
    end_dt = datetime.combine(target_date, datetime.max.time())

    sessions_stmt = (
        select(WorkoutSession)
        .where(
            and_(
                WorkoutSession.user_id == user_id,
                WorkoutSession.started_at >= start_dt,
                WorkoutSession.started_at <= end_dt,
            )
        )
    )
    sessions_result = await db.execute(sessions_stmt)
    sessions = sessions_result.scalars().all()

    lines.append("")
    lines.append("### Training:")
    if sessions:
        lines.append(f"- Workouts: {len(sessions)}")
        total_volume = sum(float(s.total_volume_kg or 0) for s in sessions)
        if total_volume > 0:
            lines.append(f"- Total volume: {total_volume:.0f} kg")
        for s in sessions:
            status_label = "Completed" if s.completed_at else "In progress"
            lines.append(f"  - {s.name or 'Workout'} ({status_label})")
    else:
        lines.append("- No training sessions today.")

    return "\n".join(lines)


async def _generate_coaching(
    client,
    metrics_summary: str,
    target_date: date,
) -> str:
    """Generates a short daily coaching message via Gemini."""

    from google.genai import types

    if client is None:
        return _fallback_coaching(metrics_summary)

    system_prompt = (
        "You are the Project Pulse AI Personal Trainer. "
        "Generate a SHORT, motivating daily coaching message (3-5 sentences max). "
        "Be specific about the user's data. Use a warm, encouraging tone. "
        "If they haven't logged much data yet, encourage them to track. "
        "If they've trained, acknowledge it. If nutrition is on track, praise it. "
        "Keep it concise — this appears as a small card on their dashboard. "
        "Use 1-2 relevant emojis. Do NOT use markdown headers or bullet points. "
        "Output plain text only."
    )

    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-3.1-flash-lite",
            contents=f"Generate a brief daily coaching message based on:\n\n{metrics_summary}",
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.8,
            ),
        )

        return response.text.strip()

    except Exception as e:
        logger.error(f"[COACHING] Gemini call failed: {e}")
        return _fallback_coaching(metrics_summary)


def _fallback_coaching(metrics_summary: str) -> str:
    """Returns a static coaching message when AI is unavailable."""
    return (
        "💪 Keep pushing today! Log your meals and workouts to get personalized "
        "AI coaching insights. Every entry helps me understand your patterns better."
    )

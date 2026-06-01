"""
Project Pulse V2 — Weekly AI Synthesis Report Router
Generates and caches AI-powered weekly fitness analysis reports.

Prefix: /api/v2/analytics
"""

import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session, get_db
from app.models.nutrition import DailyNutritionSummary
from app.models.reports import AIReport
from app.models.telemetry import DailyHealthSummary
from app.models.training import WorkoutSession, WorkoutSet
from app.routers.dependencies import get_current_user
from app.schemas.identity import ProfileResponse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v2/analytics",
    tags=["Analytics"],
)

# Track in-progress generations to prevent duplicates
_pending_generations: set[str] = set()


@router.get("/weekly-report")
async def get_weekly_report(
    week_start_date: date = Query(
        ...,
        description="Monday of the target week (YYYY-MM-DD)",
    ),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieves or generates the AI weekly synthesis report.

    - If a cached report exists for the target week, returns it immediately (200).
    - If no report exists and generation is not in progress, fires a background
      task to synthesize the report and returns 202 Accepted.
    - If generation is already in progress, returns 202 Accepted.
    """
    user_id = current_user.id
    cache_key = f"{user_id}:{week_start_date.isoformat()}"

    # Check cache
    stmt = select(AIReport).where(
        and_(
            AIReport.user_id == user_id,
            AIReport.week_start_date == week_start_date,
        )
    )
    result = await db.execute(stmt)
    report = result.scalar_one_or_none()

    if report:
        return {
            "status": "ready",
            "week_start_date": report.week_start_date.isoformat(),
            "report_markdown": report.report_markdown,
            "created_at": report.created_at.isoformat(),
        }

    # Check if generation is already in progress
    if cache_key in _pending_generations:
        return HTTPException(
            status_code=status.HTTP_202_ACCEPTED,
            detail="Report generation in progress",
        )

    # Fire background generation task
    _pending_generations.add(cache_key)
    background_tasks.add_task(
        _generate_weekly_report,
        user_id=user_id,
        week_start_date=week_start_date,
        cache_key=cache_key,
    )

    # Return 202 Accepted
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "generating",
            "week_start_date": week_start_date.isoformat(),
            "message": "Report generation started. Poll this endpoint to retrieve results.",
        },
    )


async def _generate_weekly_report(
    user_id: uuid.UUID,
    week_start_date: date,
    cache_key: str,
) -> None:
    """
    Background task: Synthesizes the weekly AI report from pre-aggregated summary tables.

    Queries:
    - daily_nutrition_summaries (7 days)
    - daily_health_summaries (7 days)
    - workout_sessions (completed in the week)

    Submits structured metrics to Gemini for coaching analysis.
    Saves the generated markdown to ai_reports.
    """
    logger.info(
        f"[REPORT] Generating weekly report for user {str(user_id)[:8]}, "
        f"week: {week_start_date}"
    )

    try:
        async with async_session() as db:
            week_end_date = week_start_date + timedelta(days=6)

            # 1. Fetch nutrition summaries
            nutrition_stmt = (
                select(DailyNutritionSummary)
                .where(
                    and_(
                        DailyNutritionSummary.user_id == user_id,
                        DailyNutritionSummary.date >= week_start_date,
                        DailyNutritionSummary.date <= week_end_date,
                    )
                )
                .order_by(DailyNutritionSummary.date.asc())
            )
            nutrition_result = await db.execute(nutrition_stmt)
            nutrition_days = nutrition_result.scalars().all()

            # 2. Fetch health summaries
            health_stmt = (
                select(DailyHealthSummary)
                .where(
                    and_(
                        DailyHealthSummary.user_id == user_id,
                        DailyHealthSummary.date >= week_start_date,
                        DailyHealthSummary.date <= week_end_date,
                    )
                )
                .order_by(DailyHealthSummary.date.asc())
            )
            health_result = await db.execute(health_stmt)
            health_days = health_result.scalars().all()

            # 3. Fetch completed workout sessions
            start_dt = datetime.combine(week_start_date, datetime.min.time())
            end_dt = datetime.combine(week_end_date, datetime.max.time())

            sessions_stmt = (
                select(WorkoutSession)
                .where(
                    and_(
                        WorkoutSession.user_id == user_id,
                        WorkoutSession.completed_at.isnot(None),
                        WorkoutSession.started_at >= start_dt,
                        WorkoutSession.started_at <= end_dt,
                    )
                )
                .order_by(WorkoutSession.started_at.asc())
            )
            sessions_result = await db.execute(sessions_stmt)
            sessions = sessions_result.scalars().all()

            # Build structured metrics summary
            metrics_summary = _build_metrics_prompt(
                nutrition_days, health_days, sessions, week_start_date, week_end_date
            )

            # Resolve the per-user Gemini client (BYOK or server-key fallback)
            # within this background task's own DB session. If neither key is
            # available, fall back to a static report rather than crashing.
            from app.services.ai import ai_service

            try:
                client = await ai_service.resolve_client(db=db, user_id=user_id)
            except RuntimeError as e:
                logger.warning(f"[REPORT] No AI client available, using fallback: {e}")
                client = None

            # 4. Generate report via Gemini
            report_markdown = await _call_gemini_for_report(client, metrics_summary)

            # 5. Save to ai_reports
            new_report = AIReport(
                id=uuid.uuid4(),
                user_id=user_id,
                week_start_date=week_start_date,
                report_markdown=report_markdown,
            )
            db.add(new_report)
            await db.commit()

            logger.info(
                f"[REPORT] Weekly report generated and cached for user {str(user_id)[:8]}"
            )

    except Exception as e:
        logger.error(f"[REPORT] Generation failed: {e}")
    finally:
        _pending_generations.discard(cache_key)


def _build_metrics_prompt(
    nutrition_days: list,
    health_days: list,
    sessions: list,
    week_start: date,
    week_end: date,
) -> str:
    """Constructs a structured metrics summary string for the AI prompt."""

    lines = [
        f"## Weekly Metrics Summary ({week_start.isoformat()} to {week_end.isoformat()})",
        "",
        "### Nutrition (Daily Summaries):",
    ]

    if nutrition_days:
        total_cal = sum(float(d.total_calories or 0) for d in nutrition_days)
        total_protein = sum(float(d.total_protein or 0) for d in nutrition_days)
        total_carbs = sum(float(d.total_carbs or 0) for d in nutrition_days)
        total_fat = sum(float(d.total_fat or 0) for d in nutrition_days)
        days_logged = len(nutrition_days)

        lines.append(f"- Days with nutrition data: {days_logged}/7")
        lines.append(f"- Average daily calories: {total_cal / max(days_logged, 1):.0f} kcal")
        lines.append(f"- Average daily protein: {total_protein / max(days_logged, 1):.0f}g")
        lines.append(f"- Average daily carbs: {total_carbs / max(days_logged, 1):.0f}g")
        lines.append(f"- Average daily fat: {total_fat / max(days_logged, 1):.0f}g")
        lines.append(f"- Total weekly calories: {total_cal:.0f} kcal")
    else:
        lines.append("- No nutrition data logged this week.")

    lines.append("")
    lines.append("### Health Telemetry (Daily Summaries):")

    if health_days:
        steps_list = [d.total_daily_steps for d in health_days if d.total_daily_steps]
        hr_list = [d.resting_heart_rate for d in health_days if d.resting_heart_rate]
        sleep_list = [d.sleep_duration_seconds for d in health_days if d.sleep_duration_seconds]

        if steps_list:
            lines.append(f"- Total weekly steps: {sum(steps_list):,}")
            lines.append(f"- Average daily steps: {sum(steps_list) // len(steps_list):,}")
        if hr_list:
            lines.append(f"- Average resting heart rate: {sum(hr_list) // len(hr_list)} bpm")
            lines.append(f"- Heart rate range: {min(hr_list)}-{max(hr_list)} bpm")
        if sleep_list:
            avg_sleep_hrs = (sum(sleep_list) / len(sleep_list)) / 3600
            lines.append(f"- Average sleep duration: {avg_sleep_hrs:.1f} hours")
    else:
        lines.append("- No health telemetry data this week.")

    lines.append("")
    lines.append("### Training (Completed Workouts):")

    if sessions:
        lines.append(f"- Total workouts completed: {len(sessions)}")
        total_volume = sum(float(s.total_volume_kg or 0) for s in sessions)
        lines.append(f"- Total volume lifted: {total_volume:.0f} kg")
        for s in sessions:
            day_name = s.started_at.strftime("%A")
            lines.append(
                f"  - {day_name}: {s.name or 'Workout'} "
                f"({float(s.total_volume_kg or 0):.0f} kg volume)"
            )
    else:
        lines.append("- No completed workouts this week.")

    return "\n".join(lines)


async def _call_gemini_for_report(client, metrics_summary: str) -> str:
    """
    Calls Gemini to generate the weekly coaching report.

    Args:
        client: A per-request resolved genai.Client (user BYOK or server key),
                produced by AIService.resolve_client. If None, falls back to a
                static report.
        metrics_summary: The structured metrics prompt.
    """

    import asyncio
    from google.genai import types

    if client is None:
        return _generate_fallback_report(metrics_summary)

    system_prompt = (
        "You are the World-Class Project Pulse AI Concierge. "
        "Analyze the user's weekly metrics, call out clear progressive overload wins in the gym, "
        "evaluate their metabolic compliance (calories/macros vs target goals), "
        "identify resting heart rate sleep quality correlations, "
        "and output a highly encouraging, structured fitness report. "
        "Utilize clean Markdown formatting with headers, bullet points, and highlight sections. "
        "Keep the tone motivating, data-driven, and actionable. "
        "Include a 'Wins This Week' section, a 'Focus Areas' section, "
        "and a 'Next Week Targets' section."
    )

    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-3.1-flash-lite",
            contents=f"Generate a weekly fitness report based on these metrics:\n\n{metrics_summary}",
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.7,
            ),
        )

        return response.text.strip()

    except Exception as e:
        logger.error(f"[REPORT] Gemini call failed: {e}")
        return _generate_fallback_report(metrics_summary)


def _generate_fallback_report(metrics_summary: str) -> str:
    """Generates a basic report when Gemini is unavailable."""
    return (
        "# Weekly Pulse Report\n\n"
        "## Summary\n\n"
        "Your AI report could not be generated at this time. "
        "Here are your raw metrics for the week:\n\n"
        f"{metrics_summary}\n\n"
        "---\n\n"
        "*Report generation will retry automatically next time you view this page.*"
    )

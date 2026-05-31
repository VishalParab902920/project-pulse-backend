"""
Project Pulse — Analytics Router
GET /api/v1/analytics — Aggregated dashboard data with AI synthesis.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.gemini import client, MODEL_NAME
from google.genai import types

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["analytics"])

MOCK_USER_ID = "00000000-0000-0000-0000-000000000001"


@router.get("/analytics")
async def get_analytics(db: Session = Depends(get_db)):
    """
    Aggregated analytics for the Bento-Box Synthesis Dashboard.
    Returns weight trend, consistency tracker, biometrics, and AI synthesis.
    """
    try:
        now = datetime.now(timezone.utc)
        seven_days_ago = now - timedelta(days=7)

        # --- Check analytics_include_assumed toggle ---
        profile = db.execute(
            sql_text("SELECT analytics_include_assumed FROM profiles WHERE id = :uid"),
            {"uid": MOCK_USER_ID},
        ).mappings().fetchone()

        include_assumed = profile["analytics_include_assumed"] if profile else False
        status_filter = "('confirmed', 'pending', 'assumed')" if include_assumed else "('confirmed', 'pending')"

        # --- Weight Trend (last 7 days) ---
        weight_rows = db.execute(
            sql_text(f"""
                SELECT
                    occurred_at::date AS day,
                    (parsed_data->>'canonical_value')::float AS weight
                FROM entries
                WHERE user_id = :uid
                  AND type = 'biometric'
                  AND parsed_data->>'metric_type' = 'body_weight'
                  AND status IN {status_filter}
                  AND occurred_at >= :since
                ORDER BY occurred_at ASC
            """),
            {"uid": MOCK_USER_ID, "since": seven_days_ago},
        ).mappings().fetchall()

        weight_trend = [
            {"day": str(r["day"]), "weight": r["weight"]}
            for r in weight_rows
        ]

        # --- Consistency Tracker (last 7 days) ---
        consistency_rows = db.execute(
            sql_text(f"""
                SELECT occurred_at::date AS day, COUNT(*) AS entry_count
                FROM entries
                WHERE user_id = :uid
                  AND status IN {status_filter}
                  AND occurred_at >= :since
                GROUP BY occurred_at::date
                ORDER BY day ASC
            """),
            {"uid": MOCK_USER_ID, "since": seven_days_ago},
        ).mappings().fetchall()

        # Build 7-day consistency array
        active_days = {str(r["day"]) for r in consistency_rows}
        consistency = []
        for i in range(7):
            day = (now - timedelta(days=6 - i)).strftime("%Y-%m-%d")
            consistency.append({"day": day, "completed": day in active_days})

        # --- Latest Biometrics (steps, heart_rate, sleep) ---
        latest_biometrics = {}
        for metric in ["steps", "heart_rate", "sleep"]:
            row = db.execute(
                sql_text(f"""
                    SELECT parsed_data->>'canonical_value' AS value, occurred_at
                    FROM entries
                    WHERE user_id = :uid
                      AND type = 'biometric'
                      AND parsed_data->>'metric_type' = :metric
                      AND status IN {status_filter}
                    ORDER BY occurred_at DESC
                    LIMIT 1
                """),
                {"uid": MOCK_USER_ID, "metric": metric},
            ).mappings().fetchone()

            if row:
                latest_biometrics[metric] = {
                    "value": float(row["value"]) if row["value"] else 0,
                    "occurred_at": str(row["occurred_at"]),
                }

        # --- Weekly Calorie Total ---
        cal_row = db.execute(
            sql_text(f"""
                SELECT COALESCE(SUM((parsed_data->'total_macros_calculated'->>'kcal')::float), 0) AS total_cals,
                       COALESCE(SUM((parsed_data->'total_macros_calculated'->>'p')::float), 0) AS total_protein
                FROM entries
                WHERE user_id = :uid
                  AND type = 'food'
                  AND status IN {status_filter}
                  AND occurred_at >= :since
            """),
            {"uid": MOCK_USER_ID, "since": seven_days_ago},
        ).mappings().fetchone()

        weekly_calories = float(cal_row["total_cals"]) if cal_row else 0
        weekly_protein = float(cal_row["total_protein"]) if cal_row else 0

        # --- AI Synthesis (coaching insight) ---
        ai_synthesis = ""
        try:
            synthesis_data = {
                "weekly_calories": round(weekly_calories),
                "weekly_protein_g": round(weekly_protein),
                "weight_entries": len(weight_trend),
                "weight_change": round(weight_trend[-1]["weight"] - weight_trend[0]["weight"], 1) if len(weight_trend) >= 2 else 0,
                "active_days": sum(1 for d in consistency if d["completed"]),
                "avg_steps": round(latest_biometrics.get("steps", {}).get("value", 0)),
                "avg_heart_rate": round(latest_biometrics.get("heart_rate", {}).get("value", 0)),
            }

            synthesis_prompt = (
                "You are our senior bio-hacking and performance coach. "
                f"Analyze this raw weekly fitness data: {json.dumps(synthesis_data)}. "
                "Write a highly concise, 2-line clinical coaching insight about their metabolic efficiency, recovery, or training. "
                "Do not exceed 30 words. Speak with authority."
            )

            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=synthesis_prompt,
                config=types.GenerateContentConfig(temperature=0.5),
            )
            ai_synthesis = response.text.strip()
            logger.info(f"[ANALYTICS] AI Synthesis: {ai_synthesis}")
        except Exception as e:
            logger.warning(f"[ANALYTICS] AI synthesis failed (non-fatal): {e}")
            ai_synthesis = "Keep logging consistently to unlock personalized insights."

        return {
            "weight_trend": weight_trend,
            "consistency": consistency,
            "latest_biometrics": latest_biometrics,
            "weekly_summary": {
                "calories": round(weekly_calories),
                "protein_g": round(weekly_protein),
            },
            "ai_synthesis": ai_synthesis,
            "include_assumed": include_assumed,
        }

    except Exception as e:
        logger.error(f"[ANALYTICS] Error: {e}")
        raise HTTPException(status_code=500, detail=f"Analytics failed: {str(e)}")

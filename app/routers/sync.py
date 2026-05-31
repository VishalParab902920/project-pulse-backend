"""
Project Pulse — Health Sync Router
POST /api/v1/sync/health — Accept biometric data from native device or browser.
"""

import json
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.entry import Entry
from app.schemas.sync import HealthSyncRequest, HealthSyncResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["sync"])

MOCK_USER_ID = "00000000-0000-0000-0000-000000000001"


@router.post("/sync/health", response_model=HealthSyncResponse)
async def sync_health(request: HealthSyncRequest, db: Session = Depends(get_db)):
    """
    Accept biometric health data and insert as confirmed entries.
    Bypasses the Review Queue since this is native hardware data.
    """
    try:
        rows_inserted = 0
        metrics_synced = []

        # Steps
        if request.steps is not None and request.steps > 0:
            entry = Entry(
                user_id=UUID(MOCK_USER_ID),
                type="biometric",
                status="confirmed",
                raw_input="[Health Sync]",
                parsed_data={
                    "metric_type": "steps",
                    "canonical_value": request.steps,
                    "canonical_unit": "steps",
                    "original_value": request.steps,
                    "original_unit": "steps",
                },
                confidence_score=1.0,
                occurred_at=request.occurred_at,
            )
            db.add(entry)
            rows_inserted += 1
            metrics_synced.append("steps")

        # Heart Rate
        if request.heart_rate_avg is not None and request.heart_rate_avg > 0:
            entry = Entry(
                user_id=UUID(MOCK_USER_ID),
                type="biometric",
                status="confirmed",
                raw_input="[Health Sync]",
                parsed_data={
                    "metric_type": "heart_rate",
                    "canonical_value": request.heart_rate_avg,
                    "canonical_unit": "bpm",
                    "original_value": request.heart_rate_avg,
                    "original_unit": "bpm",
                },
                confidence_score=1.0,
                occurred_at=request.occurred_at,
            )
            db.add(entry)
            rows_inserted += 1
            metrics_synced.append("heart_rate")

        # Sleep
        if request.sleep_minutes is not None and request.sleep_minutes > 0:
            entry = Entry(
                user_id=UUID(MOCK_USER_ID),
                type="biometric",
                status="confirmed",
                raw_input="[Health Sync]",
                parsed_data={
                    "metric_type": "sleep",
                    "canonical_value": request.sleep_minutes,
                    "canonical_unit": "minutes",
                    "original_value": request.sleep_minutes,
                    "original_unit": "minutes",
                },
                confidence_score=1.0,
                occurred_at=request.occurred_at,
            )
            db.add(entry)
            rows_inserted += 1
            metrics_synced.append("sleep")

        if rows_inserted > 0:
            db.commit()
            print(f"[Health Sync] ✓ Inserted {rows_inserted} biometric entries: {metrics_synced}")
            logger.info(f"[SYNC] Inserted {rows_inserted} rows: {metrics_synced}")

        return HealthSyncResponse(
            status="ok",
            rows_inserted=rows_inserted,
            metrics_synced=metrics_synced,
        )

    except Exception as e:
        logger.error(f"[SYNC] Error: {e}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Health sync failed: {str(e)}")

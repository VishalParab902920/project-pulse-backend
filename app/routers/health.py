"""
Project Pulse — Health Check Router
Verifies database connectivity and AI engine availability.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/health")
async def health_check(db: Session = Depends(get_db)):
    """
    Full system health check.
    - Verifies PostgreSQL connectivity via a lightweight SELECT 1 query.
    - Reports AI engine status based on whether a Gemini API key is configured.
    """
    # --- Database connectivity check ---
    db_status = "disconnected"
    try:
        db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception:
        db_status = "disconnected"

    # --- AI engine check (key configured?) ---
    ai_status = "active" if settings.gemini_api_key else "unconfigured"

    # --- Overall status ---
    overall = "optimal" if db_status == "connected" else "degraded"

    return {
        "status": overall,
        "database": db_status,
        "ai_engine": ai_status,
        "environment": settings.environment,
    }

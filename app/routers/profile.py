"""
Project Pulse — Profile Router
GET  /api/v1/profile — Retrieve current user profile.
PATCH /api/v1/profile — Update profile fields (persona, preferences, goals).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.profile import ProfileResponse, ProfilePatchRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["profile"])

# Mock user_id until Auth is integrated
MOCK_USER_ID = "00000000-0000-0000-0000-000000000001"


@router.get("/profile", response_model=ProfileResponse)
async def get_profile(db: Session = Depends(get_db)):
    """Retrieve the current user's profile."""
    result = db.execute(
        text("""
            SELECT persona_name, persona_vibe, unit_preference,
                   subscription_tier, analytics_include_assumed, onboarding_status
            FROM profiles WHERE id = :uid
        """),
        {"uid": MOCK_USER_ID},
    ).mappings().fetchone()

    if not result:
        raise HTTPException(status_code=404, detail="Profile not found")

    # Check for extended columns (daily_goals, persona_custom_instructions)
    # These may not exist yet in the DB — handle gracefully
    row = dict(result)
    return ProfileResponse(
        persona_name=row.get("persona_name", "Atlas"),
        persona_vibe=row.get("persona_vibe", "Professional Coach"),
        persona_custom_instructions=row.get("persona_custom_instructions"),
        unit_preference=row.get("unit_preference", "metric"),
        daily_goals=row.get("daily_goals"),
        subscription_tier=row.get("subscription_tier", "beta_free"),
        analytics_include_assumed=row.get("analytics_include_assumed", False),
        onboarding_status=row.get("onboarding_status", "pending"),
    )


@router.patch("/profile", response_model=ProfileResponse)
async def patch_profile(request: ProfilePatchRequest, db: Session = Depends(get_db)):
    """Update profile fields."""
    # Build dynamic SET clause from non-None fields
    updates = {}
    if request.persona_name is not None:
        updates["persona_name"] = request.persona_name
    if request.persona_vibe is not None:
        updates["persona_vibe"] = request.persona_vibe
    if request.unit_preference is not None:
        updates["unit_preference"] = request.unit_preference
    if request.analytics_include_assumed is not None:
        updates["analytics_include_assumed"] = request.analytics_include_assumed
    if request.onboarding_status is not None:
        updates["onboarding_status"] = request.onboarding_status

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Build SQL dynamically
    set_clauses = ", ".join(f"{k} = :{k}" for k in updates)
    updates["uid"] = MOCK_USER_ID

    db.execute(
        text(f"UPDATE profiles SET {set_clauses} WHERE id = :uid"),
        updates,
    )
    db.commit()

    logger.info(f"[PROFILE] Updated: {list(updates.keys())}")

    # Return updated profile
    return await get_profile(db)

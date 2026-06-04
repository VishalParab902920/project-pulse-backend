"""
Project Pulse V2 — Profile & Biometrics Router
Endpoints for user biometric onboarding, retrieval, weight logging, and BYOK key management.

Prefix: /api/v2/profile
"""

import asyncio
import gc
import io
import json
import logging
import re
import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from google.genai import types
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.identity import Profile, UserBiometric
from app.routers.dependencies import get_current_user
from app.schemas.identity import ProfileResponse, ProfileUpdate
from app.services.ai import ai_service
from app.services.security import security_service
from app.services.storage import upload_image

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v2/profile",
    tags=["Profile"],
)


# =============================================================
# Request/Response Schemas
# =============================================================


class BiometricCreateRequest(BaseModel):
    """Schema for creating/updating user biometrics during onboarding."""

    dob: date
    gender: str = Field(max_length=50)
    height_cm: float
    weight_kg: float | None = None
    body_fat_pct: float | None = None
    activity_level: str = Field(max_length=50)
    fitness_goal: str = Field(max_length=50)
    allergies: list[str] = Field(default_factory=list)
    preferred_solid_unit: str = Field(default="metric", max_length=10)
    preferred_liquid_unit: str = Field(default="metric", max_length=10)
    calculated_bmr: float
    calculated_tdee: float
    target_calories: int
    target_protein_g: int
    target_carbs_g: int
    target_fat_g: int


class BiometricResponse(BaseModel):
    """Public biometric response."""

    id: str
    user_id: str
    dob: date | None = None
    gender: str | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    activity_level: str | None = None
    fitness_goal: str | None = None
    calculated_bmr: float | None = None
    calculated_tdee: float | None = None
    target_calories: int | None = None
    target_protein_g: int | None = None
    target_carbs_g: int | None = None
    target_fat_g: int | None = None
    body_fat_pct: float | None = None
    allergies: list[str] = []
    preferred_solid_unit: str = "metric"
    preferred_liquid_unit: str = "metric"
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PreferencesUpdateRequest(BaseModel):
    """Schema for updating user preferences (units and allergies)."""

    preferred_solid_unit: str = Field(default="metric", max_length=10)
    preferred_liquid_unit: str = Field(default="metric", max_length=10)
    allergies: list[str] = Field(default_factory=list)



class BYOKUpdate(BaseModel):
    """Schema for saving a user's BYOK Gemini API key."""

    gemini_api_key: str = Field(min_length=10, max_length=200)


class WeightLogRequest(BaseModel):
    """Schema for logging a weight entry."""

    weight_kg: float = Field(gt=0, le=500)
    logged_at: date


class BodyFatEstimationResponse(BaseModel):
    """Parsed response from the ephemeral body-fat vision AI call."""

    estimated_body_fat: float = Field(
        description="Estimated body fat percentage (0.0–60.0)"
    )
    confidence_score: float = Field(
        description="Model confidence score (0.0–1.0)"
    )
    visual_critique: str = Field(
        description="Natural-language description of the visible physique composition"
    )


class BodyFatEstimationRequest(BaseModel):
    """Request schema for ephemeral body-fat estimation."""

    image_base64: str = Field(..., description="Base64 encoded body photo (JPEG/PNG/WEBP)")
    gender: str = Field(..., description="Biological sex (male/female/other)")
    height_cm: float = Field(..., gt=0, le=300, description="Height in centimetres")
    weight_kg: float = Field(..., gt=0, le=500, description="Weight in kilograms")
    age: int = Field(..., ge=1, le=120, description="Age in years")


_BODY_FAT_PROMPT_TEMPLATE = """\
Analyze the provided physical visual composition and cross-reference with the following metadata:
- Biological Sex: {gender}
- Age: {age}
- Height: {height_cm} cm
- Weight: {weight_kg} kg

Estimate the current body fat percentage based on muscle definition, vascularity, and subcutaneous fat distribution patterns visible in standard medical-grade DEXA reference categories.

Respond STRICTLY in the following clean JSON format:
{{
  "estimated_body_fat": <float>,
  "confidence_score": <float>,
  "visual_critique": "<string>"
}}"""



# =============================================================
# Biometrics Endpoints
# =============================================================


@router.post("/biometrics", response_model=BiometricResponse, status_code=status.HTTP_201_CREATED)
async def create_biometrics(
    payload: BiometricCreateRequest,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Creates or updates the user's biometric profile during onboarding.

    Inserts a new versioned row into user_biometrics and updates
    the parent profile's updated_at timestamp.
    """
    user_id = current_user.id
    logger.info(f"[PROFILE] Creating biometrics for user {str(user_id)[:8]}")

    # Fetch latest biometrics to preserve display preferences or other fields
    stmt = (
        select(UserBiometric)
        .where(UserBiometric.user_id == user_id)
        .order_by(UserBiometric.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    latest = result.scalar_one_or_none()

    preferred_solid = latest.preferred_solid_unit if latest else "metric"
    preferred_liquid = latest.preferred_liquid_unit if latest else "metric"
    primary_training = latest.primary_training_modality if latest else "general"
    manual_override = latest.manual_target_override if latest else False

    # Clamp body fat percentage if present
    body_fat = payload.body_fat_pct
    if body_fat is not None:
        body_fat = max(1.0, min(60.0, body_fat))

    # Normalize allergies: lowercase, strip whitespace, deduplicate
    # If payload.allergies is empty, fallback to latest.allergies (or empty list)
    if payload.allergies:
        normalized_allergies = list(
            set(a.strip().lower() for a in payload.allergies if a.strip())
        )
    else:
        normalized_allergies = latest.allergies if latest else []

    biometric = UserBiometric(
        id=uuid.uuid4(),
        user_id=user_id,
        dob=payload.dob,
        gender=payload.gender,
        height_cm=payload.height_cm,
        weight_kg=payload.weight_kg,
        body_fat_pct=body_fat,
        activity_level=payload.activity_level,
        fitness_goal=payload.fitness_goal,
        calculated_bmr=payload.calculated_bmr,
        calculated_tdee=payload.calculated_tdee,
        target_calories=payload.target_calories,
        target_protein_g=payload.target_protein_g,
        target_carbs_g=payload.target_carbs_g,
        target_fat_g=payload.target_fat_g,
        allergies=normalized_allergies,
        preferred_solid_unit=preferred_solid,
        preferred_liquid_unit=preferred_liquid,
        primary_training_modality=primary_training,
        manual_target_override=manual_override,
    )

    db.add(biometric)

    # Update profile timestamp using server-side now()
    await db.execute(
        update(Profile)
        .where(Profile.id == user_id)
        .values(updated_at=func.now())
    )

    await db.commit()
    await db.refresh(biometric)

    logger.info(f"[PROFILE] Biometrics saved — target: {payload.target_calories} kcal")

    return BiometricResponse(
        id=str(biometric.id),
        user_id=str(biometric.user_id),
        dob=biometric.dob,
        gender=biometric.gender,
        height_cm=float(biometric.height_cm) if biometric.height_cm else None,
        weight_kg=float(biometric.weight_kg) if biometric.weight_kg else None,
        activity_level=biometric.activity_level,
        fitness_goal=biometric.fitness_goal,
        calculated_bmr=float(biometric.calculated_bmr) if biometric.calculated_bmr else None,
        calculated_tdee=float(biometric.calculated_tdee) if biometric.calculated_tdee else None,
        target_calories=biometric.target_calories,
        target_protein_g=biometric.target_protein_g,
        target_carbs_g=biometric.target_carbs_g,
        target_fat_g=biometric.target_fat_g,
        body_fat_pct=float(biometric.body_fat_pct) if biometric.body_fat_pct else None,
        allergies=biometric.allergies or [],
        preferred_solid_unit=biometric.preferred_solid_unit,
        preferred_liquid_unit=biometric.preferred_liquid_unit,
        created_at=biometric.created_at,
        updated_at=biometric.updated_at,
    )


@router.get("/biometrics", response_model=BiometricResponse)
async def get_biometrics(
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the most recent biometrics row for the authenticated user.

    Guarantees a 200 OK response:
    - If a biometrics row exists, it is returned normally.
    - If no row exists (e.g. the DB invariant was broken during a migration),
      an emergency blank record is auto-created and returned with null fields.
      The frontend evaluates data.weight_kg / data.height_cm to determine
      whether onboarding is still required — no 404 exception needed.
    """
    user_id = current_user.id

    stmt = (
        select(UserBiometric)
        .where(UserBiometric.user_id == user_id)
        .order_by(UserBiometric.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    biometric = result.scalar_one_or_none()

    if not biometric:
        # Emergency auto-recovery: the 1:1 invariant was broken (e.g. by a
        # manual migration or data-clearing). Re-establish it silently so the
        # frontend always receives 200 OK and can evaluate null fields.
        logger.warning(
            f"[PROFILE] Biometrics row missing for user {str(user_id)[:8]} — "
            "auto-recovering blank record."
        )
        biometric = UserBiometric(user_id=user_id)
        db.add(biometric)
        await db.commit()
        await db.refresh(biometric)

    return BiometricResponse(
        id=str(biometric.id),
        user_id=str(biometric.user_id),
        dob=biometric.dob,
        gender=biometric.gender,
        height_cm=float(biometric.height_cm) if biometric.height_cm else None,
        weight_kg=float(biometric.weight_kg) if biometric.weight_kg else None,
        activity_level=biometric.activity_level,
        fitness_goal=biometric.fitness_goal,
        calculated_bmr=float(biometric.calculated_bmr) if biometric.calculated_bmr else None,
        calculated_tdee=float(biometric.calculated_tdee) if biometric.calculated_tdee else None,
        target_calories=biometric.target_calories,
        target_protein_g=biometric.target_protein_g,
        target_carbs_g=biometric.target_carbs_g,
        target_fat_g=biometric.target_fat_g,
        body_fat_pct=float(biometric.body_fat_pct) if biometric.body_fat_pct else None,
        allergies=biometric.allergies or [],
        preferred_solid_unit=biometric.preferred_solid_unit,
        preferred_liquid_unit=biometric.preferred_liquid_unit,
        created_at=biometric.created_at,
        updated_at=biometric.updated_at,
    )


@router.get("/me", response_model=ProfileResponse)
async def get_me(
    current_user: ProfileResponse = Depends(get_current_user),
):
    """Returns the current user's profile."""
    return current_user


@router.put("/me", response_model=ProfileResponse)
async def update_me(
    payload: ProfileUpdate,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Updates the user's profile (e.g., full_name, timezone)."""
    user_id = current_user.id
    stmt = select(Profile).where(Profile.id == user_id)
    result = await db.execute(stmt)
    profile = result.scalar_one_or_none()

    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")

    if payload.full_name is not None:
        profile.full_name = payload.full_name
    if payload.timezone is not None:
        profile.timezone = payload.timezone

    profile.updated_at = func.now()
    await db.commit()
    await db.refresh(profile)

    return profile



@router.post("/avatar", response_model=dict, status_code=status.HTTP_200_OK)
async def upload_avatar(
    file: UploadFile = File(..., description="Avatar photo (JPEG/PNG/WEBP)"),
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Uploads a user avatar to Supabase and links it to the profile.
    """
    user_id = current_user.id
    logger.info(f"[PROFILE] Avatar upload requested by user {str(user_id)[:8]}")

    try:
        raw_bytes = await file.read()
        mime_type = file.content_type or "image/jpeg"

        if not mime_type.startswith("image/"):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"Unsupported media type '{mime_type}'. Upload a JPEG, PNG, or WEBP image.",
            )

        public_url = await upload_image(
            image_bytes=raw_bytes,
            mime_type=mime_type,
            user_id=str(user_id),
            bucket_name="avatars",
        )

        if not public_url:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to upload image to storage.",
            )

        await db.execute(
            update(Profile)
            .where(Profile.id == user_id)
            .values(
                avatar_url=public_url,
                updated_at=func.now(),
            )
        )
        await db.commit()

        logger.info(f"[PROFILE] Avatar updated for user {str(user_id)[:8]} -> {public_url}")
        return {"avatar_url": public_url}

    except Exception as exc:
        logger.error(f"[PROFILE] Avatar upload failed: {exc}")
        if isinstance(exc, HTTPException):
            raise exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred during avatar upload.",
        )



@router.put("/onboard", response_model=BiometricResponse)
async def update_preferences(
    payload: PreferencesUpdateRequest,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Updates the user's display units and allergy list.
    Inserts a new versioned row into user_biometrics copying existing fields,
    and updates the parent profile's updated_at timestamp.
    """
    user_id = current_user.id
    logger.info(f"[PROFILE] Updating preferences for user {str(user_id)[:8]}")

    # Fetch latest biometrics
    stmt = (
        select(UserBiometric)
        .where(UserBiometric.user_id == user_id)
        .order_by(UserBiometric.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    latest = result.scalar_one_or_none()

    if not latest:
        # If no biometric record exists, we create one using default/null values
        latest = UserBiometric(user_id=user_id)

    # Normalize allergies: lowercase, strip whitespace, deduplicate
    normalized_allergies = list(
        set(a.strip().lower() for a in payload.allergies if a.strip())
    )

    new_bio = UserBiometric(
        id=uuid.uuid4(),
        user_id=user_id,
        dob=latest.dob,
        gender=latest.gender,
        height_cm=latest.height_cm,
        weight_kg=latest.weight_kg,
        body_fat_pct=latest.body_fat_pct,
        activity_level=latest.activity_level,
        fitness_goal=latest.fitness_goal,
        calculated_bmr=latest.calculated_bmr,
        calculated_tdee=latest.calculated_tdee,
        target_calories=latest.target_calories,
        target_protein_g=latest.target_protein_g,
        target_carbs_g=latest.target_carbs_g,
        target_fat_g=latest.target_fat_g,
        primary_training_modality=latest.primary_training_modality,
        manual_target_override=latest.manual_target_override,
        allergies=normalized_allergies,
        preferred_solid_unit=payload.preferred_solid_unit,
        preferred_liquid_unit=payload.preferred_liquid_unit,
    )

    db.add(new_bio)

    # Update profile timestamp using server-side now()
    await db.execute(
        update(Profile)
        .where(Profile.id == user_id)
        .values(updated_at=func.now())
    )

    await db.commit()
    await db.refresh(new_bio)

    logger.info(
        f"[PROFILE] Preferences updated for user {str(user_id)[:8]} — "
        f"solid: {payload.preferred_solid_unit}, liquid: {payload.preferred_liquid_unit}"
    )

    return BiometricResponse(
        id=str(new_bio.id),
        user_id=str(new_bio.user_id),
        dob=new_bio.dob,
        gender=new_bio.gender,
        height_cm=float(new_bio.height_cm) if new_bio.height_cm else None,
        weight_kg=float(new_bio.weight_kg) if new_bio.weight_kg else None,
        activity_level=new_bio.activity_level,
        fitness_goal=new_bio.fitness_goal,
        calculated_bmr=float(new_bio.calculated_bmr) if new_bio.calculated_bmr else None,
        calculated_tdee=float(new_bio.calculated_tdee) if new_bio.calculated_tdee else None,
        target_calories=new_bio.target_calories,
        target_protein_g=new_bio.target_protein_g,
        target_carbs_g=new_bio.target_carbs_g,
        target_fat_g=new_bio.target_fat_g,
        body_fat_pct=float(new_bio.body_fat_pct) if new_bio.body_fat_pct else None,
        allergies=new_bio.allergies or [],
        preferred_solid_unit=new_bio.preferred_solid_unit,
        preferred_liquid_unit=new_bio.preferred_liquid_unit,
        created_at=new_bio.created_at,
        updated_at=new_bio.updated_at,
    )


# =============================================================
# Weight Logging Endpoints
# =============================================================


@router.post("/biometrics/weight", status_code=status.HTTP_201_CREATED)
async def log_weight(
    payload: WeightLogRequest,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Log a weight entry. Creates a versioned biometric row preserving existing targets."""
    user_id = current_user.id

    # Get latest biometrics
    stmt = (
        select(UserBiometric)
        .where(UserBiometric.user_id == user_id)
        .order_by(UserBiometric.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    latest = result.scalar_one_or_none()

    if not latest:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No biometrics found. Complete onboarding first.",
        )

    # Create new versioned row with weight update
    # Use the user-selected date (logged_at) as the created_at timestamp
    logged_datetime = datetime.combine(payload.logged_at, datetime.min.time())
    new_bio = UserBiometric(
        id=uuid.uuid4(),
        user_id=user_id,
        dob=latest.dob,
        gender=latest.gender,
        height_cm=latest.height_cm,
        weight_kg=payload.weight_kg,
        activity_level=latest.activity_level,
        fitness_goal=latest.fitness_goal,
        calculated_bmr=latest.calculated_bmr,
        calculated_tdee=latest.calculated_tdee,
        target_calories=latest.target_calories,
        target_protein_g=latest.target_protein_g,
        target_carbs_g=latest.target_carbs_g,
        target_fat_g=latest.target_fat_g,
    )
    # Override server default with user-selected date
    new_bio.created_at = logged_datetime
    new_bio.updated_at = logged_datetime
    db.add(new_bio)
    await db.commit()
    await db.refresh(new_bio)

    logger.info(f"[PROFILE] Weight logged: {payload.weight_kg}kg for user {str(user_id)[:8]}")

    return {
        "status": "logged",
        "weight_kg": payload.weight_kg,
        "logged_at": payload.logged_at.isoformat(),
    }


@router.get("/biometrics/weight-history")
async def get_weight_history(
    days: int = Query(default=30, ge=7, le=365),
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns weight entries for the last N days.
    Deduplicates by date — keeps only the latest entry per day.
    Falls back to the user's most recent biometric row if no dedicated
    weight entries exist (e.g., new user who just completed onboarding).
    """
    user_id = current_user.id
    cutoff = date.today() - timedelta(days=days)

    stmt = (
        select(UserBiometric)
        .where(
            and_(
                UserBiometric.user_id == user_id,
                UserBiometric.weight_kg.isnot(None),
                UserBiometric.created_at >= datetime.combine(cutoff, datetime.min.time()),
            )
        )
        .order_by(UserBiometric.created_at.asc())
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()

    # Deduplicate by date — keep only the latest entry per day
    by_date: dict[str, float] = {}
    for row in rows:
        if row.weight_kg:
            day_str = row.created_at.date().isoformat()
            by_date[day_str] = float(row.weight_kg)  # later entries overwrite earlier ones

    entries = [{"date": d, "weight_kg": w} for d, w in sorted(by_date.items())]

    # Fallback: if no weight entries found, check the latest biometric row
    if not entries:
        fallback_stmt = (
            select(UserBiometric)
            .where(UserBiometric.user_id == user_id)
            .order_by(UserBiometric.created_at.desc())
            .limit(1)
        )
        fallback_result = await db.execute(fallback_stmt)
        latest_bio = fallback_result.scalar_one_or_none()
        if latest_bio and latest_bio.weight_kg:
            entries = [
                {"date": latest_bio.created_at.date().isoformat(), "weight_kg": float(latest_bio.weight_kg)}
            ]

    return entries


# =============================================================
# Ephemeral Body Fat AI Endpoint
# =============================================================


@router.post(
    "/estimate-body-fat",
    response_model=BodyFatEstimationResponse,
    status_code=status.HTTP_200_OK,
    summary="Ephemeral body-fat estimation via Gemini Vision",
    description=(
        "Accepts a JSON payload containing base64 encoded photo and metadata, and returns an AI-generated "
        "body composition estimate. The image is processed entirely in RAM and is NEVER "
        "written to disk or Supabase Storage. The binary stream is garbage-collected "
        "immediately after the Gemini call completes."
    ),
)
async def estimate_body_fat(
    payload: BodyFatEstimationRequest,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BodyFatEstimationResponse:
    """
    Ephemeral body-fat estimation pipeline.

    Flow:
        1. Decode base64 image bytes into an in-memory BytesIO buffer (no disk I/O).
        2. Resolve the per-user Gemini client (BYOK or server-key fallback).
        3. Build the specification prompt with the user's anthropometric metadata.
        4. Call gemini-2.5-flash via asyncio.to_thread (keeps the event loop unblocked).
        5. Strip any markdown fences from the raw response text.
        6. Parse + validate the JSON payload against BodyFatEstimationResponse.
        7. Close the buffer and call gc.collect() to purge binary from RAM.

    Returns 200 OK with the validated BodyFatEstimationResponse payload.
    """
    user_id = current_user.id
    logger.info(
        f"[BODY-FAT] Ephemeral estimate requested by user {str(user_id)[:8]} "
        f"— gender={payload.gender}, age={payload.age}, height={payload.height_cm}cm, weight={payload.weight_kg}kg"
    )

    # Step 1: Decode image base64 into in-memory buffer (zero disk / Supabase Storage writes)
    raw_bytes = b""
    image_buffer: io.BytesIO | None = None
    try:
        import base64

        base64_data = payload.image_base64
        if "," in base64_data:
            base64_data = base64_data.split(",")[1]

        try:
            raw_bytes = base64.b64decode(base64_data)
        except Exception as exc:
            logger.error(f"[BODY-FAT] Failed to decode base64 image: {exc}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid base64 image data.",
            )

        def detect_mime_type(data: bytes) -> str:
            if data.startswith(b"\x89PNG\r\n\x1a\n"):
                return "image/png"
            elif data.startswith(b"\xff\xd8"):
                return "image/jpeg"
            elif data.startswith(b"RIFF") and data[8:12] == b"WEBP":
                return "image/webp"
            return "image/jpeg"

        mime_type = detect_mime_type(raw_bytes)
        image_buffer = io.BytesIO(raw_bytes)

        # Step 2: Resolve BYOK-aware Gemini client
        try:
            client = await ai_service.resolve_client(db=db, user_id=user_id)
        except RuntimeError as exc:
            logger.error(f"[BODY-FAT] No Gemini client available: {exc}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI service is not configured. Set a BYOK key or contact support.",
            )

        # Step 3: Build the specification prompt
        prompt = _BODY_FAT_PROMPT_TEMPLATE.format(
            gender=payload.gender,
            age=payload.age,
            height_cm=payload.height_cm,
            weight_kg=payload.weight_kg,
        )

        # Step 4: Call Gemini 2.5 Flash in a worker thread (non-blocking)
        image_bytes = image_buffer.getvalue()
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model="gemini-2.5-flash",
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                            types.Part.from_text(text=prompt),
                        ],
                    )
                ],
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
            )
        except Exception as exc:
            logger.error(f"[BODY-FAT] Gemini call failed: {exc}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="AI model call failed. Please retry.",
            )

        # Step 5: Strip markdown fences (```json ... ```) if present
        raw_text: str = (response.text or "").strip()
        fence_match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw_text)
        if fence_match:
            raw_text = fence_match.group(1).strip()

        # Step 6: Parse + validate JSON payload
        try:
            parsed = json.loads(raw_text)
            result = BodyFatEstimationResponse(**parsed)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.error(f"[BODY-FAT] Failed to parse Gemini response: {exc} — raw: {raw_text[:200]}")
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="AI model returned an unparseable response. Please retry with a clearer image.",
            )

        logger.info(
            f"[BODY-FAT] Estimate complete for user {str(user_id)[:8]} "
            f"— body_fat={result.estimated_body_fat}%, confidence={result.confidence_score}"
        )
        return result

    finally:
        # Step 7: Purge binary stream from RAM immediately after use
        if image_buffer is not None:
            image_buffer.close()
        del raw_bytes
        gc.collect()



# =============================================================
# BYOK Endpoint
# =============================================================


@router.put("/byok")
async def save_byok_key(
    payload: BYOKUpdate,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Encrypts and stores the user's personal Gemini API key using envelope encryption.

    Flow:
        1. Retrieve the user's encrypted DEK from their profile.
        2. Decrypt the DEK using the server MASTER_KEK.
        3. Encrypt the incoming gemini_api_key using the user's raw DEK.
        4. Store the ciphertext, salt, and IV in profiles.encrypted_byok/byok_salt/byok_iv.

    Returns 200 OK on success.
    """
    user_id = current_user.id
    logger.info(f"[PROFILE] Saving BYOK key for user {str(user_id)[:8]}")

    # Step 1: Retrieve the user's profile with DEK fields
    stmt = select(Profile).where(Profile.id == user_id)
    result = await db.execute(stmt)
    profile = result.scalar_one_or_none()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User profile not found",
        )

    if not profile.encrypted_dek or not profile.dek_iv:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User encryption keys not provisioned. Re-authenticate to generate.",
        )

    # Step 2: Decrypt the user's DEK using MASTER_KEK
    try:
        raw_dek = security_service.decrypt_with_kek(
            encrypted_dek=profile.encrypted_dek,
            iv=profile.dek_iv,
            salt=profile.dek_salt,
        )
    except Exception as e:
        logger.error(f"[PROFILE] DEK decryption failed for user {str(user_id)[:8]}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to decrypt user encryption key",
        )

    # Step 3: Encrypt the incoming Gemini API key with the user's DEK
    encrypted_result = security_service.encrypt_user_data(
        plaintext_data=payload.gemini_api_key,
        user_dek=raw_dek,
    )

    # Step 4: Save to profiles table
    await db.execute(
        update(Profile)
        .where(Profile.id == user_id)
        .values(
            encrypted_byok=encrypted_result["encrypted_data"],
            byok_iv=encrypted_result["iv"],
            updated_at=func.now(),
        )
    )

    await db.commit()

    logger.info(f"[PROFILE] BYOK key encrypted and saved for user {str(user_id)[:8]}")

    return {"status": "saved", "message": "API key encrypted and stored securely."}


@router.delete("/byok")
async def delete_byok_key(
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Clears the user's BYOK Gemini API key, reverting to server-side default.
    Sets encrypted_byok and byok_iv to NULL.
    """
    user_id = current_user.id
    logger.info(f"[PROFILE] Clearing BYOK key for user {str(user_id)[:8]}")

    await db.execute(
        update(Profile)
        .where(Profile.id == user_id)
        .values(
            encrypted_byok=None,
            byok_iv=None,
            updated_at=func.now(),
        )
    )
    await db.commit()

    return {"status": "success", "message": "BYOK Key deleted. Reverted to server-side default."}

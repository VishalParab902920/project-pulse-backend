"""
Project Pulse V2 — Profile & Biometrics Router
Endpoints for user biometric onboarding, retrieval, and BYOK key management.

Prefix: /api/v2/profile
"""

import logging
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.identity import Profile, UserBiometric
from app.routers.dependencies import get_current_user
from app.schemas.identity import ProfileResponse
from app.services.security import security_service

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
    activity_level: str = Field(max_length=50)
    fitness_goal: str = Field(max_length=50)
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
    activity_level: str | None = None
    fitness_goal: str | None = None
    calculated_bmr: float | None = None
    calculated_tdee: float | None = None
    target_calories: int | None = None
    target_protein_g: int | None = None
    target_carbs_g: int | None = None
    target_fat_g: int | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BYOKUpdate(BaseModel):
    """Schema for saving a user's BYOK Gemini API key."""

    gemini_api_key: str = Field(min_length=10, max_length=200)


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

    biometric = UserBiometric(
        id=uuid.uuid4(),
        user_id=user_id,
        dob=payload.dob,
        gender=payload.gender,
        height_cm=payload.height_cm,
        activity_level=payload.activity_level,
        fitness_goal=payload.fitness_goal,
        calculated_bmr=payload.calculated_bmr,
        calculated_tdee=payload.calculated_tdee,
        target_calories=payload.target_calories,
        target_protein_g=payload.target_protein_g,
        target_carbs_g=payload.target_carbs_g,
        target_fat_g=payload.target_fat_g,
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
        activity_level=biometric.activity_level,
        fitness_goal=biometric.fitness_goal,
        calculated_bmr=float(biometric.calculated_bmr) if biometric.calculated_bmr else None,
        calculated_tdee=float(biometric.calculated_tdee) if biometric.calculated_tdee else None,
        target_calories=biometric.target_calories,
        target_protein_g=biometric.target_protein_g,
        target_carbs_g=biometric.target_carbs_g,
        target_fat_g=biometric.target_fat_g,
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
    Returns 404 if no biometrics exist (onboarding required).
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Biometrics not found. Onboarding required.",
        )

    return BiometricResponse(
        id=str(biometric.id),
        user_id=str(biometric.user_id),
        dob=biometric.dob,
        gender=biometric.gender,
        height_cm=float(biometric.height_cm) if biometric.height_cm else None,
        activity_level=biometric.activity_level,
        fitness_goal=biometric.fitness_goal,
        calculated_bmr=float(biometric.calculated_bmr) if biometric.calculated_bmr else None,
        calculated_tdee=float(biometric.calculated_tdee) if biometric.calculated_tdee else None,
        target_calories=biometric.target_calories,
        target_protein_g=biometric.target_protein_g,
        target_carbs_g=biometric.target_carbs_g,
        target_fat_g=biometric.target_fat_g,
        created_at=biometric.created_at,
        updated_at=biometric.updated_at,
    )


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

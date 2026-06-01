"""
Project Pulse V2 — Identity & Security Pydantic Schemas
DTOs for: profiles, user_biometrics, user_integrations

Security Note: Raw encryption fields (encrypted_dek, dek_salt, dek_iv)
and decrypted credentials are NEVER exposed in response schemas.
"""

import uuid
from datetime import date, datetime

from pydantic import Field

from app.schemas.base import BaseSchema


# =============================================================
# Profile Schemas
# =============================================================


class ProfileBase(BaseSchema):
    """Shared profile fields for create/update operations."""

    timezone: str = Field(default="UTC", max_length=100)


class ProfileCreate(ProfileBase):
    """Schema for creating a new profile. ID comes from auth.users."""

    id: uuid.UUID


class ProfileUpdate(ProfileBase):
    """Schema for updating profile settings."""

    timezone: str | None = None


class ProfileResponse(ProfileBase):
    """
    Public profile response. NEVER exposes encryption fields.
    """

    id: uuid.UUID
    timezone: str
    created_at: datetime
    updated_at: datetime


# =============================================================
# User Biometric Schemas
# =============================================================


class UserBiometricBase(BaseSchema):
    """Shared biometric fields."""

    dob: date | None = None
    gender: str | None = Field(default=None, max_length=50)
    height_cm: float | None = None
    activity_level: str | None = Field(default=None, max_length=50)
    fitness_goal: str | None = Field(default=None, max_length=50)
    calculated_bmr: float | None = None
    calculated_tdee: float | None = None
    target_calories: int | None = None
    target_protein_g: int | None = None
    target_carbs_g: int | None = None
    target_fat_g: int | None = None


class UserBiometricCreate(UserBiometricBase):
    """Schema for creating user biometrics. user_id set from auth context."""

    pass


class UserBiometricUpdate(UserBiometricBase):
    """Schema for updating user biometrics. All fields optional."""

    dob: date | None = None
    height_cm: float | None = None
    activity_level: str | None = None
    fitness_goal: str | None = None
    target_calories: int | None = None
    target_protein_g: int | None = None
    target_carbs_g: int | None = None
    target_fat_g: int | None = None


class UserBiometricResponse(UserBiometricBase):
    """Public biometric response."""

    id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# =============================================================
# User Integration Schemas
# =============================================================


class UserIntegrationBase(BaseSchema):
    """Shared integration fields."""

    provider: str = Field(max_length=100)
    is_active: bool = True


class UserIntegrationCreate(UserIntegrationBase):
    """
    Schema for creating an integration.
    Accepts plaintext credentials which will be encrypted server-side.
    """

    credentials: str = Field(description="Plaintext credentials to be encrypted with user DEK")


class UserIntegrationUpdate(BaseSchema):
    """Schema for updating an integration."""

    is_active: bool | None = None
    credentials: str | None = Field(
        default=None,
        description="New plaintext credentials to re-encrypt",
    )


class UserIntegrationResponse(UserIntegrationBase):
    """
    Public integration response. NEVER exposes decrypted credentials.
    Only shows provider name and active status.
    """

    id: uuid.UUID
    user_id: uuid.UUID
    provider: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

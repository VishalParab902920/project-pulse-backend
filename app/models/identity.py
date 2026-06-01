"""
Project Pulse V2 — Domain 1: Identity & Security Models
Maps: profiles, user_biometrics, user_integrations
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Profile(Base):
    """
    Maps to `profiles` table.
    Links to auth.users via PK-FK. Contains envelope encryption fields
    for the user's unique Data Encryption Key (DEK).
    """

    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    encrypted_dek: Mapped[str | None] = mapped_column(Text, nullable=True)
    dek_salt: Mapped[str | None] = mapped_column(Text, nullable=True)
    dek_iv: Mapped[str | None] = mapped_column(Text, nullable=True)
    # BYOK (Bring Your Own Key) — user's Gemini API key encrypted with their DEK.
    # Stored as an envelope: ciphertext (encrypted_byok) + 12-byte IV (byok_iv).
    # byok_salt is reserved for future KDF use (currently unused by encrypt_user_data).
    encrypted_byok: Mapped[str | None] = mapped_column(Text, nullable=True)
    byok_salt: Mapped[str | None] = mapped_column(Text, nullable=True)
    byok_iv: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone: Mapped[str] = mapped_column(
        String(100), nullable=False, default="UTC", server_default="UTC"
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )

    # Relationships
    biometric: Mapped["UserBiometric | None"] = relationship(
        "UserBiometric", back_populates="profile", uselist=False, lazy="selectin"
    )
    integrations: Mapped[list["UserIntegration"]] = relationship(
        "UserIntegration", back_populates="profile", lazy="selectin"
    )
    nutrition_logs: Mapped[list["NutritionLog"]] = relationship(
        "NutritionLog", back_populates="profile", lazy="noload"
    )
    recipes: Mapped[list["Recipe"]] = relationship(
        "Recipe", back_populates="profile", lazy="noload"
    )
    workout_sessions: Mapped[list["WorkoutSession"]] = relationship(
        "WorkoutSession", back_populates="profile", lazy="noload"
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        "Conversation", back_populates="profile", lazy="noload"
    )


class UserBiometric(Base):
    """
    Maps to `user_biometrics` table.
    Stores demographic targets: gender, DOB, height, activity level,
    fitness goal, calculated BMR/TDEE, and target macronutrients.
    """

    __tablename__ = "user_biometrics"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    dob: Mapped[date | None] = mapped_column(Date, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(50), nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    activity_level: Mapped[str | None] = mapped_column(String(50), nullable=True)
    fitness_goal: Mapped[str | None] = mapped_column(String(50), nullable=True)
    calculated_bmr: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    calculated_tdee: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    target_calories: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_protein_g: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_carbs_g: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_fat_g: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )

    # Relationships
    profile: Mapped["Profile"] = relationship(
        "Profile", back_populates="biometric"
    )


class UserIntegration(Base):
    """
    Maps to `user_integrations` table.
    Stores encrypted credentials for third-party wearable sync providers.
    Credentials are encrypted using the user's decrypted DEK.
    """

    __tablename__ = "user_integrations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    encrypted_credentials: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )

    # Relationships
    profile: Mapped["Profile"] = relationship(
        "Profile", back_populates="integrations"
    )

"""
Project Pulse V2.5 — Domain 2: Nutrition Models
Tables: foods, food_measures, nutrition_logs_v2,
        recipes, recipe_ingredients, daily_nutrition_summaries
"""

import uuid
from datetime import date, datetime
from typing import List

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# =============================================================
# Unified Food Table
# =============================================================


class Food(Base):
    """Unified food reference table (formerly food_dictionary + foods)."""

    __tablename__ = "foods"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=lambda: uuid.uuid4()
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(100), nullable=True)
    barcode: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    base_unit: Mapped[str] = mapped_column(String(10), nullable=False, default="g")
    calories_per_100: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    protein_per_100: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    carbs_per_100: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    fat_per_100: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    # Relationships
    measures: Mapped[List["FoodMeasure"]] = relationship(
        "FoodMeasure", back_populates="food", cascade="all, delete-orphan", lazy="selectin"
    )
    recipe_ingredients: Mapped[list["RecipeIngredient"]] = relationship(
        "RecipeIngredient", back_populates="food", lazy="noload"
    )

    __table_args__ = (
        CheckConstraint("base_unit IN ('g', 'ml')", name="ck_foods_base_unit"),
        Index("idx_foods_barcode", "barcode", unique=True, postgresql_where=Column("barcode").isnot(None)),
        Index("idx_foods_verified_search", "is_verified", "name", "brand"),
    )


class FoodMeasure(Base):
    """Measurement units for a food item."""

    __tablename__ = "food_measures"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=lambda: uuid.uuid4()
    )
    food_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("foods.id", ondelete="CASCADE"), nullable=False
    )
    measure_name: Mapped[str] = mapped_column(String(50), nullable=False)
    conversion_factor: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    food: Mapped["Food"] = relationship("Food", back_populates="measures")

    __table_args__ = (
        UniqueConstraint("food_id", "measure_name", name="uq_food_measures_food_name"),
        Index("idx_food_measures_food_id", "food_id"),
    )


# =============================================================
# Recipes
# =============================================================


class Recipe(Base):
    """Maps to `recipes` table."""

    __tablename__ = "recipes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default="now()")

    # Relationships
    ingredients: Mapped[list["RecipeIngredient"]] = relationship(
        "RecipeIngredient", back_populates="recipe", lazy="selectin", cascade="all, delete-orphan"
    )
    profile: Mapped["Profile"] = relationship("Profile", back_populates="recipes", lazy="noload")


class RecipeIngredient(Base):
    """Maps to `recipe_ingredients`. Compound PK: (recipe_id, food_id)."""

    __tablename__ = "recipe_ingredients"

    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recipes.id", ondelete="CASCADE"), primary_key=True
    )
    food_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("foods.id", ondelete="CASCADE"), primary_key=True
    )
    weight_g: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)

    # Relationships
    recipe: Mapped["Recipe"] = relationship("Recipe", back_populates="ingredients")
    food: Mapped["Food"] = relationship("Food", back_populates="recipe_ingredients")


# =============================================================
# Daily Nutrition Summary
# =============================================================


class DailyNutritionSummary(Base):
    """Maps to `daily_nutrition_summaries`. Compound PK: (user_id, date)."""

    __tablename__ = "daily_nutrition_summaries"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    total_calories: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    total_protein: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    total_carbs: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    total_fat: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    total_water_ml: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default="now()")


# =============================================================
# Nutrition Logs (V2.5)
# =============================================================


class NutritionLog(Base):
    """User food log with pre-calculated nutrition values."""

    __tablename__ = "nutrition_logs_v2"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=lambda: uuid.uuid4()
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    meal_type: Mapped[str] = mapped_column(String(50), nullable=False)
    food_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("foods.id", ondelete="CASCADE"), nullable=False
    )
    measure_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("food_measures.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    # Pre-calculated denormalized fields
    calculated_qty_base: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    calculated_calories: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    calculated_protein: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    calculated_carbs: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    calculated_fat: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    # Relationships
    food: Mapped["Food"] = relationship("Food", lazy="selectin")
    measure: Mapped["FoodMeasure"] = relationship("FoodMeasure", lazy="selectin")
    profile: Mapped["Profile"] = relationship("Profile", back_populates="nutrition_logs", lazy="noload")

    __table_args__ = (
        Index("idx_nutrition_logs_user_date", "user_id", "logged_at"),
        Index("idx_nutrition_logs_food_id", "food_id"),
        Index("idx_nutrition_logs_measure_id", "measure_id"),
    )


# Forward reference
from app.models.identity import Profile  # noqa: E402, F401

"""
Project Pulse V2 — Domain 2: Nutrition Models
Maps: food_dictionary, recipes, recipe_ingredients, nutrition_logs, daily_nutrition_summaries
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class FoodDictionary(Base):
    """
    Maps to `food_dictionary` table.
    Canonical index of global foods plus user-submitted custom foods.
    """

    __tablename__ = "food_dictionary"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(255), nullable=True)
    calories_per_100g: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    protein_per_100g: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    carbs_per_100g: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    fat_per_100g: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )

    # Relationships
    recipe_ingredients: Mapped[list["RecipeIngredient"]] = relationship(
        "RecipeIngredient", back_populates="food", lazy="noload"
    )
    nutrition_logs: Mapped[list["NutritionLog"]] = relationship(
        "NutritionLog", back_populates="food", lazy="noload"
    )


class Recipe(Base):
    """
    Maps to `recipes` table.
    Grouping table for user-created custom recipes.
    """

    __tablename__ = "recipes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )

    # Relationships
    profile: Mapped["Profile"] = relationship(
        "Profile", back_populates="recipes"
    )
    ingredients: Mapped[list["RecipeIngredient"]] = relationship(
        "RecipeIngredient", back_populates="recipe", lazy="selectin", cascade="all, delete-orphan"
    )
    nutrition_logs: Mapped[list["NutritionLog"]] = relationship(
        "NutritionLog", back_populates="recipe", lazy="noload"
    )


class RecipeIngredient(Base):
    """
    Maps to `recipe_ingredients` table.
    Join table mapping recipes to food_dictionary with weight in grams.
    Compound PK: (recipe_id, food_id).
    """

    __tablename__ = "recipe_ingredients"

    recipe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recipes.id", ondelete="CASCADE"),
        primary_key=True,
    )
    food_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("food_dictionary.id", ondelete="CASCADE"),
        primary_key=True,
    )
    weight_g: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)

    # Relationships
    recipe: Mapped["Recipe"] = relationship(
        "Recipe", back_populates="ingredients"
    )
    food: Mapped["FoodDictionary"] = relationship(
        "FoodDictionary", back_populates="recipe_ingredients"
    )


class NutritionLog(Base):
    """
    Maps to `nutrition_logs` table.
    Daily tracking ledger for food and recipe consumption.
    """

    __tablename__ = "nutrition_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=True,
    )
    logged_at: Mapped[datetime] = mapped_column(nullable=False)
    meal_type: Mapped[str] = mapped_column(String(50), nullable=False)
    food_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("food_dictionary.id", ondelete="SET NULL"),
        nullable=True,
    )
    recipe_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("recipes.id", ondelete="SET NULL"),
        nullable=True,
    )
    serving_size_g: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )

    # Relationships
    profile: Mapped["Profile"] = relationship(
        "Profile", back_populates="nutrition_logs"
    )
    food: Mapped["FoodDictionary | None"] = relationship(
        "FoodDictionary", back_populates="nutrition_logs"
    )
    recipe: Mapped["Recipe | None"] = relationship(
        "Recipe", back_populates="nutrition_logs"
    )


class DailyNutritionSummary(Base):
    """
    Maps to `daily_nutrition_summaries` table.
    Permanent aggregate table preserving daily nutrition performance.
    Compound PK: (user_id, date).
    """

    __tablename__ = "daily_nutrition_summaries"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    total_calories: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    total_protein: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    total_carbs: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    total_fat: Mapped[float | None] = mapped_column(Numeric(6, 2), nullable=True)
    total_water_ml: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )


# Forward reference imports for type checking
from app.models.identity import Profile  # noqa: E402, F401

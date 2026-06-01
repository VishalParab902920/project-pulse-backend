"""
Project Pulse V2 — Nutrition Pydantic Schemas
DTOs for: food_dictionary, recipes, recipe_ingredients,
          nutrition_logs, daily_nutrition_summaries
"""

import uuid
from datetime import date, datetime

from pydantic import Field

from app.schemas.base import BaseSchema


# =============================================================
# Food Dictionary Schemas
# =============================================================


class FoodDictionaryBase(BaseSchema):
    """Shared food dictionary fields."""

    name: str = Field(max_length=255)
    brand: str | None = Field(default=None, max_length=255)
    calories_per_100g: float
    protein_per_100g: float
    carbs_per_100g: float
    fat_per_100g: float
    is_verified: bool = False
    barcode: str | None = None


class FoodDictionaryCreate(FoodDictionaryBase):
    """Schema for creating a food entry."""

    pass


class FoodDictionaryUpdate(BaseSchema):
    """Schema for updating a food entry."""

    name: str | None = Field(default=None, max_length=255)
    brand: str | None = Field(default=None, max_length=255)
    calories_per_100g: float | None = None
    protein_per_100g: float | None = None
    carbs_per_100g: float | None = None
    fat_per_100g: float | None = None


class FoodDictionaryResponse(FoodDictionaryBase):
    """Public food dictionary response."""

    id: uuid.UUID
    user_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


# =============================================================
# Recipe Schemas
# =============================================================


class RecipeIngredientBase(BaseSchema):
    """Shared recipe ingredient fields."""

    food_id: uuid.UUID
    weight_g: float


class RecipeIngredientCreate(RecipeIngredientBase):
    """Schema for adding an ingredient to a recipe."""

    pass


class RecipeIngredientResponse(RecipeIngredientBase):
    """Recipe ingredient response with nested food details."""

    recipe_id: uuid.UUID
    food: FoodDictionaryResponse | None = None


class RecipeBase(BaseSchema):
    """Shared recipe fields."""

    title: str = Field(max_length=255)
    instructions: str | None = None


class RecipeCreate(RecipeBase):
    """Schema for creating a recipe with ingredients."""

    ingredients: list[RecipeIngredientCreate] = Field(default_factory=list)


class RecipeUpdate(BaseSchema):
    """Schema for updating a recipe."""

    title: str | None = Field(default=None, max_length=255)
    instructions: str | None = None
    ingredients: list[RecipeIngredientCreate] | None = None


class RecipeResponse(RecipeBase):
    """Public recipe response with nested ingredients."""

    id: uuid.UUID
    user_id: uuid.UUID | None = None
    ingredients: list[RecipeIngredientResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# =============================================================
# Nutrition Log Schemas
# =============================================================


class NutritionLogBase(BaseSchema):
    """Shared nutrition log fields."""

    logged_at: datetime
    meal_type: str = Field(max_length=50)
    food_id: uuid.UUID | None = None
    recipe_id: uuid.UUID | None = None
    serving_size_g: float


class NutritionLogCreate(NutritionLogBase):
    """Schema for creating a nutrition log entry."""

    pass


class NutritionLogUpdate(BaseSchema):
    """Schema for updating a nutrition log entry."""

    logged_at: datetime | None = None
    meal_type: str | None = Field(default=None, max_length=50)
    food_id: uuid.UUID | None = None
    recipe_id: uuid.UUID | None = None
    serving_size_g: float | None = None


class NutritionLogResponse(NutritionLogBase):
    """Public nutrition log response with optional nested food/recipe."""

    id: uuid.UUID
    user_id: uuid.UUID | None = None
    food: FoodDictionaryResponse | None = None
    recipe: RecipeResponse | None = None
    created_at: datetime
    updated_at: datetime


# =============================================================
# Daily Nutrition Summary Schemas
# =============================================================


class DailyNutritionSummaryBase(BaseSchema):
    """Shared daily nutrition summary fields."""

    date: date
    total_calories: float | None = None
    total_protein: float | None = None
    total_carbs: float | None = None
    total_fat: float | None = None
    total_water_ml: int | None = None


class DailyNutritionSummaryResponse(DailyNutritionSummaryBase):
    """Public daily nutrition summary response."""

    user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

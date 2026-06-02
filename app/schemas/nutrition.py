"""
Project Pulse V2.5 — Nutrition Pydantic Schemas (Consolidated)
Contains both legacy schemas (AI flow) and V2.5 normalized schemas.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import List, Literal, Optional

from pydantic import Field, field_validator

from app.schemas.base import BaseSchema


# =============================================================
# Legacy Schemas (DEPRECATED — kept for backward compatibility)
# The `food_dictionary` table has been consolidated into `foods`.
# Prefer FoodCreate / FoodResponse for new code.
# =============================================================


class FoodDictionaryBase(BaseSchema):
    """Shared food dictionary fields. DEPRECATED: use FoodCreate/FoodResponse."""
    name: str = Field(max_length=255)
    brand: str | None = Field(default=None, max_length=255)
    calories_per_100g: float
    protein_per_100g: float
    carbs_per_100g: float
    fat_per_100g: float
    is_verified: bool = False
    barcode: str | None = None


class FoodDictionaryCreate(FoodDictionaryBase):
    pass


class FoodDictionaryUpdate(BaseSchema):
    name: str | None = Field(default=None, max_length=255)
    brand: str | None = Field(default=None, max_length=255)
    calories_per_100g: float | None = None
    protein_per_100g: float | None = None
    carbs_per_100g: float | None = None
    fat_per_100g: float | None = None


class FoodDictionaryResponse(FoodDictionaryBase):
    id: uuid.UUID
    user_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime


class RecipeIngredientBase(BaseSchema):
    food_id: uuid.UUID
    weight_g: float


class RecipeIngredientCreate(RecipeIngredientBase):
    pass


class RecipeIngredientResponse(RecipeIngredientBase):
    recipe_id: uuid.UUID
    food: FoodDictionaryResponse | None = None


class RecipeBase(BaseSchema):
    title: str = Field(max_length=255)
    instructions: str | None = None


class RecipeCreate(RecipeBase):
    ingredients: list[RecipeIngredientCreate] = Field(default_factory=list)


class RecipeUpdate(BaseSchema):
    title: str | None = Field(default=None, max_length=255)
    instructions: str | None = None
    ingredients: list[RecipeIngredientCreate] | None = None


class RecipeResponse(RecipeBase):
    id: uuid.UUID
    user_id: uuid.UUID | None = None
    ingredients: list[RecipeIngredientResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class NutritionLogLegacyBase(BaseSchema):
    """Legacy nutrition log fields (AI flow)."""
    logged_at: datetime
    meal_type: str = Field(max_length=50)
    food_id: uuid.UUID | None = None
    recipe_id: uuid.UUID | None = None
    serving_size_g: float


class NutritionLogLegacyCreate(NutritionLogLegacyBase):
    pass


class NutritionLogLegacyUpdate(BaseSchema):
    logged_at: datetime | None = None
    meal_type: str | None = Field(default=None, max_length=50)
    food_id: uuid.UUID | None = None
    recipe_id: uuid.UUID | None = None
    serving_size_g: float | None = None


class NutritionLogLegacyResponse(NutritionLogLegacyBase):
    id: uuid.UUID
    user_id: uuid.UUID | None = None
    food: FoodDictionaryResponse | None = None
    recipe: RecipeResponse | None = None
    created_at: datetime
    updated_at: datetime


# Backward-compat aliases for AI router imports
NutritionLogCreate = NutritionLogLegacyCreate
NutritionLogUpdate = NutritionLogLegacyUpdate
NutritionLogResponse = NutritionLogLegacyResponse


class DailyNutritionSummaryBase(BaseSchema):
    date: date
    total_calories: float | None = None
    total_protein: float | None = None
    total_carbs: float | None = None
    total_fat: float | None = None
    total_water_ml: int | None = None


class DailyNutritionSummaryResponse(DailyNutritionSummaryBase):
    user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# =============================================================
# V2.5 Normalized Schemas
# =============================================================


class FoodMeasureCreate(BaseSchema):
    """Schema for creating a custom food measure."""
    measure_name: str = Field(min_length=1, max_length=50)
    conversion_factor: Decimal = Field(gt=Decimal("0.0"))

    @field_validator("measure_name", mode="before")
    @classmethod
    def strip_measure_name(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                raise ValueError("measure_name must not be blank")
        return v


class FoodMeasureResponse(BaseSchema):
    """Public food measure response."""
    id: uuid.UUID
    food_id: uuid.UUID
    measure_name: str
    conversion_factor: Decimal
    is_default: bool


class FoodCreate(BaseSchema):
    """Schema for creating a food item with optional custom measures."""
    name: str = Field(min_length=1, max_length=255)
    brand: Optional[str] = None
    barcode: Optional[str] = None
    base_unit: Literal["g", "ml"] = "g"
    calories_per_100: Decimal = Field(ge=Decimal("0.0"))
    protein_per_100: Decimal = Field(ge=Decimal("0.0"))
    carbs_per_100: Decimal = Field(ge=Decimal("0.0"))
    fat_per_100: Decimal = Field(ge=Decimal("0.0"))
    is_custom: bool = False
    measures: List[FoodMeasureCreate] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
            if not v:
                raise ValueError("name must not be blank")
        return v


class FoodResponse(BaseSchema):
    """Public food response with resolved measures."""
    id: uuid.UUID
    name: str
    brand: Optional[str] = None
    barcode: Optional[str] = None
    base_unit: str
    calories_per_100: Decimal
    protein_per_100: Decimal
    carbs_per_100: Decimal
    fat_per_100: Decimal
    is_custom: bool
    is_verified: bool
    created_by: Optional[uuid.UUID] = None
    measures: List[FoodMeasureResponse] = Field(default_factory=list)


class DiaryLogCreate(BaseSchema):
    """Schema for creating a V2.5 diary log entry."""
    food_id: uuid.UUID
    measure_id: uuid.UUID
    quantity: Decimal = Field(gt=Decimal("0.0"))
    logged_at: datetime
    meal_type: Literal["breakfast", "lunch", "dinner", "snack"]


class DiaryLogResponse(BaseSchema):
    """Full V2.5 diary log response with nested context."""
    id: uuid.UUID
    user_id: uuid.UUID
    logged_at: datetime
    meal_type: str
    food_id: uuid.UUID
    measure_id: uuid.UUID
    quantity: Decimal
    food: Optional[FoodResponse] = None
    measure: Optional[FoodMeasureResponse] = None
    calculated_qty_base: Decimal
    calculated_calories: Decimal
    calculated_protein: Decimal
    calculated_carbs: Decimal
    calculated_fat: Decimal

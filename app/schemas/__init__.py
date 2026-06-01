"""
Project Pulse V2 — Pydantic Schema Registry
"""

from app.schemas.base import BaseSchema
from app.schemas.identity import (
    ProfileBase,
    ProfileCreate,
    ProfileResponse,
    ProfileUpdate,
    UserBiometricBase,
    UserBiometricCreate,
    UserBiometricResponse,
    UserBiometricUpdate,
    UserIntegrationBase,
    UserIntegrationCreate,
    UserIntegrationResponse,
    UserIntegrationUpdate,
)
from app.schemas.nutrition import (
    DailyNutritionSummaryResponse,
    FoodDictionaryCreate,
    FoodDictionaryResponse,
    FoodDictionaryUpdate,
    NutritionLogCreate,
    NutritionLogResponse,
    NutritionLogUpdate,
    RecipeCreate,
    RecipeIngredientCreate,
    RecipeIngredientResponse,
    RecipeResponse,
    RecipeUpdate,
)
from app.schemas.training import (
    ExerciseCreate,
    ExerciseResponse,
    ExerciseUpdate,
    WorkoutSessionCreate,
    WorkoutSessionResponse,
    WorkoutSessionUpdate,
    WorkoutSetCreate,
    WorkoutSetResponse,
    WorkoutSetUpdate,
    WorkoutTemplateCreate,
    WorkoutTemplateResponse,
    WorkoutTemplateUpdate,
)
from app.schemas.telemetry import (
    DailyHealthSummaryResponse,
    HealthMetricBatchCreate,
    HealthMetricCreate,
    HealthMetricResponse,
)

__all__ = [
    "BaseSchema",
    # Identity
    "ProfileBase",
    "ProfileCreate",
    "ProfileResponse",
    "ProfileUpdate",
    "UserBiometricBase",
    "UserBiometricCreate",
    "UserBiometricResponse",
    "UserBiometricUpdate",
    "UserIntegrationBase",
    "UserIntegrationCreate",
    "UserIntegrationResponse",
    "UserIntegrationUpdate",
    # Nutrition
    "FoodDictionaryCreate",
    "FoodDictionaryResponse",
    "FoodDictionaryUpdate",
    "NutritionLogCreate",
    "NutritionLogResponse",
    "NutritionLogUpdate",
    "RecipeCreate",
    "RecipeIngredientCreate",
    "RecipeIngredientResponse",
    "RecipeResponse",
    "RecipeUpdate",
    "DailyNutritionSummaryResponse",
    # Training
    "ExerciseCreate",
    "ExerciseResponse",
    "ExerciseUpdate",
    "WorkoutTemplateCreate",
    "WorkoutTemplateResponse",
    "WorkoutTemplateUpdate",
    "WorkoutSessionCreate",
    "WorkoutSessionResponse",
    "WorkoutSessionUpdate",
    "WorkoutSetCreate",
    "WorkoutSetResponse",
    "WorkoutSetUpdate",
    # Telemetry
    "HealthMetricCreate",
    "HealthMetricBatchCreate",
    "HealthMetricResponse",
    "DailyHealthSummaryResponse",
]

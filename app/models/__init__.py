"""
Project Pulse V2 — ORM Model Registry
Imports all models to ensure they are registered with Base.metadata.
"""

from app.models.identity import Profile, UserBiometric, UserIntegration
from app.models.nutrition import (
    DailyNutritionSummary,
    FoodDictionary,
    NutritionLog,
    Recipe,
    RecipeIngredient,
)
from app.models.training import Exercise, WorkoutSession, WorkoutSet, WorkoutTemplate
from app.models.telemetry import DailyHealthSummary, HealthMetric
from app.models.ai import ChatMessage, Conversation, SemanticMemory
from app.models.reports import AIReport

__all__ = [
    # Domain 1: Identity & Security
    "Profile",
    "UserBiometric",
    "UserIntegration",
    # Domain 2: Nutrition
    "FoodDictionary",
    "Recipe",
    "RecipeIngredient",
    "NutritionLog",
    "DailyNutritionSummary",
    # Domain 3: Training
    "Exercise",
    "WorkoutTemplate",
    "WorkoutSession",
    "WorkoutSet",
    # Domain 4: Telemetry
    "HealthMetric",
    "DailyHealthSummary",
    # Domain 5: AI & Memory
    "Conversation",
    "ChatMessage",
    "SemanticMemory",
    # Reports
    "AIReport",
]

"""
Project Pulse V2.5 — ORM Model Registry
"""

from app.models.identity import Profile, UserBiometric, UserIntegration
from app.models.nutrition import (
    DailyNutritionSummary,
    Food,
    FoodMeasure,
    NutritionLog,
    Recipe,
    RecipeIngredient,
)
from app.models.training import Exercise, WorkoutSession, WorkoutSet, WorkoutTemplate
from app.models.telemetry import DailyHealthSummary, HealthMetric
from app.models.ai import ChatMessage, Conversation, SemanticMemory
from app.models.reports import AIReport

__all__ = [
    "Profile",
    "UserBiometric",
    "UserIntegration",
    "Food",
    "FoodMeasure",
    "Recipe",
    "RecipeIngredient",
    "NutritionLog",
    "DailyNutritionSummary",
    "Exercise",
    "WorkoutTemplate",
    "WorkoutSession",
    "WorkoutSet",
    "HealthMetric",
    "DailyHealthSummary",
    "Conversation",
    "ChatMessage",
    "SemanticMemory",
    "AIReport",
]

"""
Project Pulse V2 — Nutrition Service Layer
Orchestrates food resolution, nutrition logging, diary retrieval,
and daily summary aggregation.

Implements:
    - Entity resolution for food items (exact match → fuzzy → AI-estimated insert)
    - Nutrition log creation with food/recipe binding
    - Daily diary retrieval grouped by meal type
    - Daily nutrition summary upsert aggregation
"""

import logging
import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import and_, cast, Date, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.nutrition import (
    DailyNutritionSummary,
    FoodDictionary,
    NutritionLog,
    Recipe,
)
from app.schemas.nutrition import (
    DailyNutritionSummaryResponse,
    FoodDictionaryResponse,
    NutritionLogCreate,
    NutritionLogResponse,
)

logger = logging.getLogger(__name__)


class NutritionService:
    """
    Service layer for nutrition tracking operations.

    Handles food entity resolution, log creation, diary retrieval,
    and daily summary aggregation with eager-loaded relationships.
    """

    async def resolve_food_item(
        self,
        db: AsyncSession,
        name: str,
        ai_estimated_macros: dict | None = None,
    ) -> FoodDictionary:
        """
        Entity Resolution Engine for food items.

        Resolution strategy:
            1. Exact case-insensitive match on food_dictionary.name
            2. Partial ILIKE match for close variants
            3. If no match found and AI macros provided, insert as unverified entry

        Args:
            db: Async database session.
            name: The food name to resolve.
            ai_estimated_macros: Optional dict with keys:
                calories_per_100g, protein_per_100g, carbs_per_100g, fat_per_100g

        Returns:
            FoodDictionary: The resolved or newly created food record.
        """
        logger.info(f"[NUTRITION] Resolving food item: '{name}'")

        # Step 1: Exact case-insensitive match
        stmt = select(FoodDictionary).where(
            func.lower(FoodDictionary.name) == func.lower(name)
        )
        result = await db.execute(stmt)
        food = result.scalar_one_or_none()

        if food:
            logger.info(f"[NUTRITION] Exact match found: {food.name} ({food.id})")
            return food

        # Step 2: Partial ILIKE match (fuzzy name search)
        fuzzy_stmt = (
            select(FoodDictionary)
            .where(FoodDictionary.name.ilike(f"%{name}%"))
            .limit(1)
        )
        fuzzy_result = await db.execute(fuzzy_stmt)
        fuzzy_food = fuzzy_result.scalar_one_or_none()

        if fuzzy_food:
            logger.info(
                f"[NUTRITION] Fuzzy match found: '{fuzzy_food.name}' for query '{name}'"
            )
            return fuzzy_food

        # Step 3: No match — insert new entry if AI macros are provided
        if ai_estimated_macros:
            new_food = FoodDictionary(
                id=uuid.uuid4(),
                name=name.strip().title(),
                brand=None,
                calories_per_100g=ai_estimated_macros.get("calories_per_100g", 0),
                protein_per_100g=ai_estimated_macros.get("protein_per_100g", 0),
                carbs_per_100g=ai_estimated_macros.get("carbs_per_100g", 0),
                fat_per_100g=ai_estimated_macros.get("fat_per_100g", 0),
                is_verified=False,
                user_id=None,
            )
            db.add(new_food)
            await db.flush()
            logger.info(
                f"[NUTRITION] Created unverified food entry: '{new_food.name}' "
                f"({new_food.calories_per_100g} kcal/100g)"
            )
            return new_food

        # Step 4: No match and no AI macros — create minimal placeholder
        placeholder_food = FoodDictionary(
            id=uuid.uuid4(),
            name=name.strip().title(),
            brand=None,
            calories_per_100g=0,
            protein_per_100g=0,
            carbs_per_100g=0,
            fat_per_100g=0,
            is_verified=False,
            user_id=None,
        )
        db.add(placeholder_food)
        await db.flush()
        logger.warning(
            f"[NUTRITION] Created placeholder food (no macros): '{placeholder_food.name}'"
        )
        return placeholder_food

    async def log_nutrition(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        log_data: NutritionLogCreate,
    ) -> NutritionLogResponse:
        """
        Creates a nutrition log entry bound to the authenticated user.

        Resolves the food/recipe entity reference, persists the log record,
        and returns the fully serialized response with eager-loaded relations.

        Args:
            db: Async database session.
            user_id: The authenticated user's UUID.
            log_data: Validated nutrition log creation data.

        Returns:
            NutritionLogResponse: The created log with nested food/recipe details.
        """
        logger.info(
            f"[NUTRITION] Logging entry for user {str(user_id)[:8]} — "
            f"meal: {log_data.meal_type}, serving: {log_data.serving_size_g}g"
        )

        # Create the nutrition log record
        nutrition_log = NutritionLog(
            id=uuid.uuid4(),
            user_id=user_id,
            logged_at=log_data.logged_at,
            meal_type=log_data.meal_type,
            food_id=log_data.food_id,
            recipe_id=log_data.recipe_id,
            serving_size_g=log_data.serving_size_g,
        )

        db.add(nutrition_log)
        await db.commit()

        # Re-fetch with eager-loaded relationships for response serialization
        stmt = (
            select(NutritionLog)
            .options(
                joinedload(NutritionLog.food),
                joinedload(NutritionLog.recipe).joinedload(Recipe.ingredients),
            )
            .where(NutritionLog.id == nutrition_log.id)
        )
        result = await db.execute(stmt)
        loaded_log = result.unique().scalar_one()

        logger.info(f"[NUTRITION] Log created: {loaded_log.id}")
        return NutritionLogResponse.model_validate(loaded_log)

    async def get_daily_diary(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        target_date: date,
    ) -> dict[str, list[NutritionLogResponse]]:
        """
        Retrieves the full nutrition diary for a specific date, grouped by meal type.

        Eagerly loads food_dictionary and recipe relationships to prevent N+1 queries.
        Groups entries into: Breakfast, Lunch, Dinner, Snacks.

        Args:
            db: Async database session.
            user_id: The authenticated user's UUID.
            target_date: The calendar date to retrieve logs for.

        Returns:
            dict: Keys are meal categories, values are lists of NutritionLogResponse.
                  {
                      "breakfast": [...],
                      "lunch": [...],
                      "dinner": [...],
                      "snack": [...]
                  }
        """
        logger.info(
            f"[NUTRITION] Fetching diary for user {str(user_id)[:8]} on {target_date}"
        )

        # Build date range for the target day (naive datetimes for asyncpg compatibility)
        day_start = datetime.combine(target_date, time.min)
        day_end = datetime.combine(target_date, time.max)

        stmt = (
            select(NutritionLog)
            .options(
                joinedload(NutritionLog.food),
                joinedload(NutritionLog.recipe).joinedload(Recipe.ingredients),
            )
            .where(
                and_(
                    NutritionLog.user_id == user_id,
                    NutritionLog.logged_at >= day_start,
                    NutritionLog.logged_at <= day_end,
                )
            )
            .order_by(NutritionLog.logged_at.asc())
        )

        result = await db.execute(stmt)
        logs = result.unique().scalars().all()

        # Group by meal_type
        diary: dict[str, list[NutritionLogResponse]] = {
            "breakfast": [],
            "lunch": [],
            "dinner": [],
            "snack": [],
        }

        for log in logs:
            meal_key = log.meal_type.lower()
            if meal_key not in diary:
                meal_key = "snack"  # Default unknown meal types to snack
            diary[meal_key].append(NutritionLogResponse.model_validate(log))

        total_entries = sum(len(v) for v in diary.values())
        logger.info(
            f"[NUTRITION] Diary retrieved: {total_entries} entries across "
            f"{sum(1 for v in diary.values() if v)} meal categories"
        )
        return diary

    async def update_daily_nutrition_summary(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        target_date: date,
    ) -> DailyNutritionSummaryResponse | None:
        """
        Aggregates all nutrition logs for a user on a specific date and upserts
        the computed totals into daily_nutrition_summaries.

        Calculates:
            - total_calories: sum of (food.calories_per_100g * serving_size_g / 100)
            - total_protein: sum of (food.protein_per_100g * serving_size_g / 100)
            - total_carbs: sum of (food.carbs_per_100g * serving_size_g / 100)
            - total_fat: sum of (food.fat_per_100g * serving_size_g / 100)

        Args:
            db: Async database session.
            user_id: The authenticated user's UUID.
            target_date: The calendar date to aggregate.

        Returns:
            DailyNutritionSummaryResponse | None: The upserted summary, or None if no logs.
        """
        logger.info(
            f"[NUTRITION] Aggregating daily summary for user {str(user_id)[:8]} "
            f"on {target_date}"
        )

        # Build date range
        day_start = datetime.combine(target_date, time.min)
        day_end = datetime.combine(target_date, time.max)

        # Aggregate query joining nutrition_logs with food_dictionary
        agg_stmt = (
            select(
                func.sum(
                    FoodDictionary.calories_per_100g * NutritionLog.serving_size_g / 100
                ).label("total_calories"),
                func.sum(
                    FoodDictionary.protein_per_100g * NutritionLog.serving_size_g / 100
                ).label("total_protein"),
                func.sum(
                    FoodDictionary.carbs_per_100g * NutritionLog.serving_size_g / 100
                ).label("total_carbs"),
                func.sum(
                    FoodDictionary.fat_per_100g * NutritionLog.serving_size_g / 100
                ).label("total_fat"),
            )
            .select_from(NutritionLog)
            .join(FoodDictionary, NutritionLog.food_id == FoodDictionary.id, isouter=True)
            .where(
                and_(
                    NutritionLog.user_id == user_id,
                    NutritionLog.logged_at >= day_start,
                    NutritionLog.logged_at <= day_end,
                )
            )
        )

        result = await db.execute(agg_stmt)
        row = result.one_or_none()

        if row is None or row.total_calories is None:
            logger.info("[NUTRITION] No logs found for aggregation")
            return None

        # Upsert into daily_nutrition_summaries
        existing_stmt = select(DailyNutritionSummary).where(
            and_(
                DailyNutritionSummary.user_id == user_id,
                DailyNutritionSummary.date == target_date,
            )
        )
        existing_result = await db.execute(existing_stmt)
        summary = existing_result.scalar_one_or_none()

        total_calories = float(row.total_calories) if row.total_calories else 0.0
        total_protein = float(row.total_protein) if row.total_protein else 0.0
        total_carbs = float(row.total_carbs) if row.total_carbs else 0.0
        total_fat = float(row.total_fat) if row.total_fat else 0.0

        if summary:
            # Update existing summary
            summary.total_calories = total_calories
            summary.total_protein = total_protein
            summary.total_carbs = total_carbs
            summary.total_fat = total_fat
        else:
            # Insert new summary
            summary = DailyNutritionSummary(
                user_id=user_id,
                date=target_date,
                total_calories=total_calories,
                total_protein=total_protein,
                total_carbs=total_carbs,
                total_fat=total_fat,
                total_water_ml=None,
            )
            db.add(summary)

        await db.commit()
        await db.refresh(summary)

        logger.info(
            f"[NUTRITION] Summary upserted: {total_calories:.0f} kcal, "
            f"{total_protein:.0f}g P, {total_carbs:.0f}g C, {total_fat:.0f}g F"
        )
        return DailyNutritionSummaryResponse.model_validate(summary)

    async def delete_nutrition_log(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        log_id: uuid.UUID,
    ) -> bool:
        """
        Deletes a nutrition log entry owned by the user.

        Args:
            db: Async database session.
            user_id: The authenticated user's UUID.
            log_id: The nutrition log ID to delete.

        Returns:
            bool: True if deleted, False if not found or not owned.
        """
        stmt = select(NutritionLog).where(
            and_(
                NutritionLog.id == log_id,
                NutritionLog.user_id == user_id,
            )
        )
        result = await db.execute(stmt)
        log = result.scalar_one_or_none()

        if not log:
            logger.warning(f"[NUTRITION] Log {log_id} not found for user {str(user_id)[:8]}")
            return False

        await db.delete(log)
        await db.commit()
        logger.info(f"[NUTRITION] Deleted log {log_id}")
        return True


# Module-level singleton for dependency injection
nutrition_service = NutritionService()

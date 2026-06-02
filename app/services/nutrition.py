"""
Project Pulse V2.5 — Nutrition Service Layer (Consolidated)
Handles: food entity resolution (AI), food creation,
diary logging with pre-calculated macros, daily timeline retrieval.
All operations use the unified `foods` table.
"""

import logging
import uuid
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.nutrition import (
    DailyNutritionSummary,
    Food,
    FoodMeasure,
    NutritionLog,
)
from app.schemas.nutrition import (
    DailyNutritionSummaryResponse,
    DiaryLogCreate,
    DiaryLogResponse,
    FoodCreate,
    FoodResponse,
    NutritionLogCreate,
    NutritionLogResponse,
)

logger = logging.getLogger(__name__)

TWO_PLACES = Decimal("0.01")


class NutritionService:
    """Consolidated nutrition service with both legacy AI and V2.5 operations."""

    # =============================================================
    # Legacy AI Flow Methods
    # =============================================================

    async def resolve_food_item(
        self,
        db: AsyncSession,
        name: str,
        ai_estimated_macros: dict | None = None,
    ) -> Food:
        """
        Entity Resolution Engine for food items (AI parsing flow).
        Uses the unified `foods` table.
        1. Exact case-insensitive match
        2. Partial ILIKE match
        3. Insert as unverified if AI macros provided
        """
        logger.info(f"[NUTRITION] Resolving food item: '{name}'")

        stmt = select(Food).where(
            func.lower(Food.name) == func.lower(name)
        )
        result = await db.execute(stmt)
        food = result.scalar_one_or_none()
        if food:
            return food

        fuzzy_stmt = (
            select(Food)
            .where(Food.name.ilike(f"%{name}%"))
            .limit(1)
        )
        fuzzy_result = await db.execute(fuzzy_stmt)
        fuzzy_food = fuzzy_result.scalar_one_or_none()
        if fuzzy_food:
            return fuzzy_food

        if ai_estimated_macros:
            new_food = Food(
                id=uuid.uuid4(),
                name=name.strip().title(),
                brand=None,
                base_unit="g",
                calories_per_100=ai_estimated_macros.get("calories_per_100", 0),
                protein_per_100=ai_estimated_macros.get("protein_per_100", 0),
                carbs_per_100=ai_estimated_macros.get("carbs_per_100", 0),
                fat_per_100=ai_estimated_macros.get("fat_per_100", 0),
                is_verified=False,
                is_custom=False,
                created_by=None,
            )
            db.add(new_food)
            await db.flush()
            return new_food

        placeholder_food = Food(
            id=uuid.uuid4(),
            name=name.strip().title(),
            brand=None,
            base_unit="g",
            calories_per_100=0,
            protein_per_100=0,
            carbs_per_100=0,
            fat_per_100=0,
            is_verified=False,
            is_custom=False,
            created_by=None,
        )
        db.add(placeholder_food)
        await db.flush()
        return placeholder_food

    async def log_nutrition(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        log_data: NutritionLogCreate,
    ) -> NutritionLogResponse:
        """
        Legacy log creation (AI flow). Writes to nutrition_logs_v2
        using the unified foods table macros and default 'g' measure.
        """
        # Resolve food from unified foods table
        food = await db.get(Food, log_data.food_id)
        if not food:
            raise ValueError(f"Food not found: {log_data.food_id}")

        # Get or create default 'g' measure
        measure_stmt = select(FoodMeasure).where(
            and_(FoodMeasure.food_id == food.id, FoodMeasure.measure_name == "g")
        )
        measure_result = await db.execute(measure_stmt)
        measure = measure_result.scalar_one_or_none()

        if not measure:
            measure = FoodMeasure(
                id=uuid.uuid4(),
                food_id=food.id,
                measure_name="g",
                conversion_factor=1.0,
                is_default=True,
            )
            db.add(measure)
            await db.flush()

        # Calculate macros
        serving = Decimal(str(log_data.serving_size_g))
        cal = (serving / Decimal("100.0") * Decimal(str(food.calories_per_100))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        pro = (serving / Decimal("100.0") * Decimal(str(food.protein_per_100))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        carb = (serving / Decimal("100.0") * Decimal(str(food.carbs_per_100))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        fat = (serving / Decimal("100.0") * Decimal(str(food.fat_per_100))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        log_entry = NutritionLog(
            id=uuid.uuid4(),
            user_id=user_id,
            logged_at=log_data.logged_at,
            meal_type=log_data.meal_type,
            food_id=food.id,
            measure_id=measure.id,
            quantity=float(serving),
            calculated_qty_base=float(serving),
            calculated_calories=float(cal),
            calculated_protein=float(pro),
            calculated_carbs=float(carb),
            calculated_fat=float(fat),
        )
        db.add(log_entry)
        await db.commit()

        # Return legacy-shaped response
        return NutritionLogResponse(
            id=log_entry.id,
            user_id=user_id,
            logged_at=log_data.logged_at,
            meal_type=log_data.meal_type,
            food_id=log_data.food_id,
            recipe_id=log_data.recipe_id,
            serving_size_g=log_data.serving_size_g,
            created_at=log_entry.created_at or datetime.utcnow(),
            updated_at=log_entry.created_at or datetime.utcnow(),
        )

    async def delete_nutrition_log(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        log_id: uuid.UUID,
    ) -> bool:
        """Deletes a nutrition log entry owned by the user."""
        stmt = select(NutritionLog).where(
            and_(NutritionLog.id == log_id, NutritionLog.user_id == user_id)
        )
        result = await db.execute(stmt)
        log = result.scalar_one_or_none()

        if not log:
            return False

        await db.delete(log)
        await db.commit()
        return True

    # =============================================================
    # V2.5 Methods
    # =============================================================

    async def create_food(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        payload: FoodCreate,
    ) -> FoodResponse:
        """Create food record with default + custom measures."""
        food_id = uuid.uuid4()

        food = Food(
            id=food_id,
            name=payload.name.strip(),
            brand=payload.brand,
            barcode=payload.barcode,
            base_unit=payload.base_unit,
            calories_per_100=float(payload.calories_per_100),
            protein_per_100=float(payload.protein_per_100),
            carbs_per_100=float(payload.carbs_per_100),
            fat_per_100=float(payload.fat_per_100),
            is_custom=payload.is_custom,
            created_by=user_id,
        )
        db.add(food)

        default_measure = FoodMeasure(
            id=uuid.uuid4(),
            food_id=food_id,
            measure_name=payload.base_unit,
            conversion_factor=1.0,
            is_default=True,
        )
        db.add(default_measure)

        for m in payload.measures:
            if m.measure_name.strip().lower() == payload.base_unit:
                continue
            db.add(FoodMeasure(
                id=uuid.uuid4(),
                food_id=food_id,
                measure_name=m.measure_name.strip(),
                conversion_factor=float(m.conversion_factor),
                is_default=False,
            ))

        await db.commit()

        stmt = select(Food).where(Food.id == food_id).options(selectinload(Food.measures))
        result = await db.execute(stmt)
        created_food = result.scalar_one()

        return FoodResponse.model_validate(created_food)

    async def create_diary_log(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        payload: DiaryLogCreate,
    ) -> DiaryLogResponse:
        """Create diary log entry with pre-calculated macros."""
        food = await db.get(Food, payload.food_id)
        if not food:
            raise ValueError(f"Food not found: {payload.food_id}")

        measure = await db.get(FoodMeasure, payload.measure_id)
        if not measure:
            raise ValueError(f"Measure not found: {payload.measure_id}")

        if measure.food_id != food.id:
            raise ValueError(f"Measure {payload.measure_id} does not belong to food {payload.food_id}")

        quantity = Decimal(str(payload.quantity))
        conversion_factor = Decimal(str(measure.conversion_factor))
        base_qty = (quantity * conversion_factor).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        calculated_calories = (base_qty / Decimal("100.0") * Decimal(str(food.calories_per_100))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        calculated_protein = (base_qty / Decimal("100.0") * Decimal(str(food.protein_per_100))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        calculated_carbs = (base_qty / Decimal("100.0") * Decimal(str(food.carbs_per_100))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        calculated_fat = (base_qty / Decimal("100.0") * Decimal(str(food.fat_per_100))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

        log_entry = NutritionLog(
            id=uuid.uuid4(),
            user_id=user_id,
            logged_at=payload.logged_at,
            meal_type=payload.meal_type,
            food_id=payload.food_id,
            measure_id=payload.measure_id,
            quantity=float(quantity),
            calculated_qty_base=float(base_qty),
            calculated_calories=float(calculated_calories),
            calculated_protein=float(calculated_protein),
            calculated_carbs=float(calculated_carbs),
            calculated_fat=float(calculated_fat),
        )
        db.add(log_entry)
        await db.commit()

        stmt = (
            select(NutritionLog)
            .where(NutritionLog.id == log_entry.id)
            .options(
                selectinload(NutritionLog.food).selectinload(Food.measures),
                selectinload(NutritionLog.measure),
            )
        )
        result = await db.execute(stmt)
        loaded_log = result.scalar_one()

        return DiaryLogResponse.model_validate(loaded_log)

    async def get_daily_diary(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        target_date: date,
    ) -> list[DiaryLogResponse]:
        """Fetch all V2.5 diary logs for a user on a specific date."""
        day_start = datetime.combine(target_date, time.min)
        day_end = datetime.combine(target_date, time.max)

        stmt = (
            select(NutritionLog)
            .where(
                and_(
                    NutritionLog.user_id == user_id,
                    NutritionLog.logged_at >= day_start,
                    NutritionLog.logged_at <= day_end,
                )
            )
            .options(
                selectinload(NutritionLog.food).selectinload(Food.measures),
                selectinload(NutritionLog.measure),
            )
            .order_by(NutritionLog.logged_at.asc())
        )

        result = await db.execute(stmt)
        logs = result.scalars().all()

        return [DiaryLogResponse.model_validate(log) for log in logs]


# Module-level singleton
nutrition_service = NutritionService()

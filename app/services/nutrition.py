"""
Project Pulse V2.5 — Nutrition Service Layer (Consolidated)
Handles: food entity resolution (AI), food creation,
diary logging with pre-calculated macros, daily timeline retrieval.
All operations use the unified `foods` table.

v2.5.2 additions:
  - MetabolicCalculator: deterministic BMR/TDEE engine (Katch-McArdle + Mifflin-St Jeor)
    with goal-based macro split distributions.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

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

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Physical Activity Level (PAL) multipliers per PLAN.md §2.1 point 3
_PAL_MULTIPLIERS: dict[str, Decimal] = {
    "sedentary":           Decimal("1.200"),
    "lightly_active":      Decimal("1.375"),
    "moderately_active":   Decimal("1.550"),
    "highly_active":       Decimal("1.725"),
    "competitive_athlete": Decimal("1.900"),
}

# Goal-based calorie delta (kcal) and macro split ratios per PLAN.md §2.1 point 4
# Each entry: (calorie_delta, protein_ratio, carbs_ratio, fat_ratio)
_GOAL_SPLITS: dict[str, tuple[int, Decimal, Decimal, Decimal]] = {
    "extreme_cut":     (-750, Decimal("0.45"), Decimal("0.25"), Decimal("0.30")),
    "cut":             (-500, Decimal("0.40"), Decimal("0.30"), Decimal("0.30")),
    "maintain":        (   0, Decimal("0.30"), Decimal("0.40"), Decimal("0.30")),
    "lean_bulk":       ( 300, Decimal("0.30"), Decimal("0.45"), Decimal("0.25")),
    "aggressive_bulk": ( 500, Decimal("0.25"), Decimal("0.50"), Decimal("0.25")),
}

# Kcal per gram constants
_KCAL_PER_G_PROTEIN = Decimal("4")
_KCAL_PER_G_CARBS   = Decimal("4")
_KCAL_PER_G_FAT     = Decimal("9")

# body_fat_pct clamping bounds
_BF_MIN = Decimal("1.0")
_BF_MAX = Decimal("60.0")

# Fallback age when DOB is missing or unparseable
_FALLBACK_AGE = 30


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetCalculationResult:
    """
    Immutable result produced by MetabolicCalculator.calculate().

    All Decimal values are rounded to 2 decimal places.
    Macro gram values are whole-number integers (rounded from kcal/macroRatio).
    ``None`` for all fields signals that manual_target_override is active.
    """

    bmr: Decimal
    """Basal Metabolic Rate in kcal/day."""

    tdee: Decimal
    """Total Daily Energy Expenditure in kcal/day."""

    target_calories: int
    """Adjusted daily calorie target (TDEE ± goal delta)."""

    target_protein_g: int
    """Daily protein target in grams."""

    target_carbs_g: int
    """Daily carbohydrate target in grams."""

    target_fat_g: int
    """Daily fat target in grams."""


# ---------------------------------------------------------------------------
# Calculator
# ---------------------------------------------------------------------------


class MetabolicCalculator:
    """
    Deterministic BMR/TDEE calculation engine for Kayan v2.5.2.

    All arithmetic uses ``decimal.Decimal`` (never raw ``float``) to guarantee
    reproducible rounding regardless of platform FP implementation.

    This class is stateless and contains no database I/O, making it safe to
    call from any async context without blocking FastAPI's event loop.

    Usage::

        result = MetabolicCalculator.calculate(
            weight_kg=74.5,
            height_cm=181.0,
            dob=date(1996, 5, 12),
            gender="male",
            activity_level="moderately_active",
            fitness_goal="lean_bulk",
            body_fat_pct=15.2,
        )
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @classmethod
    def calculate(
        cls,
        weight_kg: float,
        height_cm: float,
        dob: Optional[date],
        gender: str,
        activity_level: str,
        fitness_goal: str,
        body_fat_pct: Optional[float] = None,
        manual_target_override: bool = False,
    ) -> Optional["TargetCalculationResult"]:
        """
        Calculate BMR, TDEE, and goal-adjusted macro targets.

        Parameters
        ----------
        weight_kg:
            Current body weight in kilograms.
        height_cm:
            Standing height in centimetres.
        dob:
            Date of birth for Mifflin-St Jeor age term. Falls back to
            ``_FALLBACK_AGE`` (30) with a WARNING log if ``None`` or invalid.
        gender:
            Biological sex string. ``'male'`` triggers the +5 Mifflin constant;
            any other value uses −161.
        activity_level:
            One of the keys in ``_PAL_MULTIPLIERS``. Unmapped keys default to
            ``'sedentary'`` with a WARNING log.
        fitness_goal:
            One of ``'cut'``, ``'recomp'``, ``'lean_bulk'``. Unmapped keys
            default to ``'recomp'`` with a WARNING log.
        body_fat_pct:
            Optional body fat percentage. When provided, selects the
            Katch-McArdle formula. Clamped to [1.0, 60.0] before use.
        manual_target_override:
            When ``True``, returns ``None`` — the caller must preserve the
            user's manually configured targets without recalculating.

        Returns
        -------
        TargetCalculationResult or None
            ``None`` iff ``manual_target_override`` is ``True``.
        """
        if manual_target_override:
            logger.debug(
                "[MetabolicCalculator] manual_target_override=True — skipping recalculation."
            )
            return None

        w = Decimal(str(weight_kg))
        h = Decimal(str(height_cm))

        bmr = cls._compute_bmr(w, h, dob, gender, body_fat_pct)
        tdee = cls._compute_tdee(bmr, activity_level)
        return cls._apply_goal_split(bmr, tdee, fitness_goal)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @classmethod
    def _compute_bmr(
        cls,
        weight_kg: Decimal,
        height_cm: Decimal,
        dob: Optional[date],
        gender: str,
        body_fat_pct: Optional[float],
    ) -> Decimal:
        """Select and compute BMR via Katch-McArdle or Mifflin-St Jeor."""

        if body_fat_pct is not None:
            return cls._katch_mcardle(weight_kg, body_fat_pct)

        age = cls._safe_age(dob)
        return cls._mifflin_st_jeor(weight_kg, height_cm, age, gender)

    @staticmethod
    def _katch_mcardle(weight_kg: Decimal, body_fat_pct: float) -> Decimal:
        """
        Katch-McArdle Formula.

        LBM (kg) = weight_kg × (1 − body_fat_pct / 100)
        BMR = 370 + (21.6 × LBM)
        """
        # Clamp body_fat_pct to valid physiological range
        bf = Decimal(str(body_fat_pct))
        if bf < _BF_MIN or bf > _BF_MAX:
            original = bf
            bf = max(_BF_MIN, min(_BF_MAX, bf))
            logger.warning(
                "[MetabolicCalculator] body_fat_pct %.2f is outside [1.0, 60.0]; "
                "clamped to %.2f.",
                float(original),
                float(bf),
            )

        lbm = weight_kg * (Decimal("1") - bf / Decimal("100"))
        bmr = Decimal("370") + (Decimal("21.6") * lbm)
        return bmr.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    @staticmethod
    def _mifflin_st_jeor(
        weight_kg: Decimal,
        height_cm: Decimal,
        age: int,
        gender: str,
    ) -> Decimal:
        """
        Mifflin-St Jeor Formula.

        Male:   BMR = (10 × weight) + (6.25 × height) − (5 × age) + 5
        Female: BMR = (10 × weight) + (6.25 × height) − (5 × age) − 161
        """
        age_d = Decimal(str(age))
        base = (
            Decimal("10") * weight_kg
            + Decimal("6.25") * height_cm
            - Decimal("5") * age_d
        )
        gender_constant = Decimal("5") if gender.lower() == "male" else Decimal("-161")
        bmr = base + gender_constant
        return bmr.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    @staticmethod
    def _compute_tdee(bmr: Decimal, activity_level: str) -> Decimal:
        """Multiply BMR by the PAL factor for the given activity level."""
        multiplier = _PAL_MULTIPLIERS.get(activity_level)
        if multiplier is None:
            logger.warning(
                "[MetabolicCalculator] Unknown activity_level '%s'; defaulting to 'sedentary'.",
                activity_level,
            )
            multiplier = _PAL_MULTIPLIERS["sedentary"]

        tdee = bmr * multiplier
        return tdee.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    @staticmethod
    def _apply_goal_split(bmr: Decimal, tdee: Decimal, fitness_goal: str) -> TargetCalculationResult:
        """
        Apply goal-based calorie delta and derive macro gram targets.

        Macro grams are computed from calorie percentages then integer-rounded:
          protein_g = round(target_calories * protein_ratio / 4)
          carbs_g   = round(target_calories * carbs_ratio   / 4)
          fat_g     = round(target_calories * fat_ratio     / 9)
        """
        split = _GOAL_SPLITS.get(fitness_goal)
        if split is None:
            logger.warning(
                "[MetabolicCalculator] Unknown fitness_goal '%s'; defaulting to 'maintain'.",
                fitness_goal,
            )
            split = _GOAL_SPLITS["maintain"]

        calorie_delta, protein_ratio, carbs_ratio, fat_ratio = split
        target_calories_d = tdee + Decimal(str(calorie_delta))

        protein_g = int(
            (target_calories_d * protein_ratio / _KCAL_PER_G_PROTEIN)
            .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        carbs_g = int(
            (target_calories_d * carbs_ratio / _KCAL_PER_G_CARBS)
            .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        fat_g = int(
            (target_calories_d * fat_ratio / _KCAL_PER_G_FAT)
            .quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )

        return TargetCalculationResult(
            bmr=bmr,
            tdee=tdee,
            target_calories=int(
                target_calories_d.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            ),
            target_protein_g=protein_g,
            target_carbs_g=carbs_g,
            target_fat_g=fat_g,
        )

    @staticmethod
    def _safe_age(dob: Optional[date]) -> int:
        """
        Derive age in whole years from a date of birth.

        Falls back to ``_FALLBACK_AGE`` (30) with a WARNING if:
        - ``dob`` is ``None``
        - ``dob`` is a future date (invalid for this context)
        - Any unexpected exception occurs during calculation
        """
        if dob is None:
            logger.warning(
                "[MetabolicCalculator] DOB is None; using fallback age %d for "
                "Mifflin-St Jeor calculation.",
                _FALLBACK_AGE,
            )
            return _FALLBACK_AGE

        try:
            today = date.today()
            age = (
                today.year
                - dob.year
                - ((today.month, today.day) < (dob.month, dob.day))
            )
            if age <= 0:
                logger.warning(
                    "[MetabolicCalculator] DOB %s resolves to age %d (≤ 0); "
                    "using fallback age %d.",
                    dob,
                    age,
                    _FALLBACK_AGE,
                )
                return _FALLBACK_AGE
            return age
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[MetabolicCalculator] Could not compute age from DOB '%s': %s. "
                "Using fallback age %d.",
                dob,
                exc,
                _FALLBACK_AGE,
            )
            return _FALLBACK_AGE


# Module-level singleton for convenience
metabolic_calculator = MetabolicCalculator()


# ---------------------------------------------------------------------------


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
            # v2.5.2: persist the user-supplied allergen tags array
            allergens=[a.strip().lower() for a in payload.allergens if a.strip()],
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

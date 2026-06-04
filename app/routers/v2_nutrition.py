"""
Project Pulse V2.5 — Nutrition Router (Consolidated)
Endpoints: food CRUD, diary CRUD, water, recipes, barcode search.

Prefix: /api/v2/nutrition
"""

import logging
import uuid
from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.identity import UserBiometric
from app.models.nutrition import (
    DailyNutritionSummary,
    Food,
    FoodMeasure,
    Recipe,
    RecipeIngredient,
)
from app.routers.dependencies import get_current_user
from app.schemas.identity import ProfileResponse
from app.schemas.nutrition import (
    DiaryLogCreate,
    DiaryLogResponse,
    FoodCreate,
    FoodResponse,
)
from app.services.nutrition import nutrition_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v2/nutrition",
    tags=["Nutrition"],
)


# =============================================================
# V2.5 Core Endpoints
# =============================================================


@router.post("/food", response_model=FoodResponse, status_code=status.HTTP_201_CREATED)
async def create_food(
    payload: FoodCreate,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a food item with default + custom measures in one transaction."""
    logger.info(f"[API] POST /nutrition/food — user: {str(current_user.id)[:8]}, food: '{payload.name}'")

    result = await nutrition_service.create_food(
        db=db,
        user_id=current_user.id,
        payload=payload,
    )
    return result


@router.post("/diary", response_model=DiaryLogResponse, status_code=status.HTTP_201_CREATED)
async def create_diary_entry(
    payload: DiaryLogCreate,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    x_allergen_override: str | None = Header(
        default=None,
        alias="X-Allergen-Override",
        description="Set to 'true' to bypass allergen safety block and force-log the entry.",
    ),
):
    """Create a diary log entry with pre-calculated macros.

    Before writing the log, an allergen interceptor cross-references the food's
    `allergens` array against the user's `user_biometrics.allergies` profile.

    - If an intersection is found AND the header ``X-Allergen-Override: true`` is
      absent, the request is rejected with ``409 Conflict``.
    - If the override header is present, the log is written and a warning is
      emitted to stdout.
    - If the user has no biometric row or an empty allergies list, the check is
      bypassed immediately (null-safety fast path).
    """
    logger.info(
        f"[API] POST /nutrition/diary — user: {str(current_user.id)[:8]}, "
        f"meal: {payload.meal_type}, qty: {payload.quantity}"
    )

    # --- Allergen Interceptor (v2.5.2) ---
    # Fetch the latest biometric row to read user allergies
    bio_stmt = (
        select(UserBiometric)
        .where(UserBiometric.user_id == current_user.id)
        .order_by(UserBiometric.created_at.desc())
        .limit(1)
    )
    bio_result = await db.execute(bio_stmt)
    biometric = bio_result.scalar_one_or_none()

    user_allergies: list[str] = []
    if biometric and biometric.allergies:
        user_allergies = [a.strip().lower() for a in biometric.allergies if a.strip()]

    # Null-safety bypass: skip evaluation if the user has no known allergies
    if user_allergies:
        food_for_check = await db.get(Food, payload.food_id)
        if food_for_check and food_for_check.allergens:
            food_allergens = {a.strip().lower() for a in food_for_check.allergens if a.strip()}
            offending = food_allergens & set(user_allergies)

            if offending:
                override_active = (
                    x_allergen_override is not None
                    and x_allergen_override.strip().lower() == "true"
                )

                if not override_active:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail={
                            "error_code": "ALLERGEN_DETECTED",
                            "offending_allergens": sorted(offending),
                        },
                    )

                # Override is active — log the allergen collision warning to stdout
                print(
                    f"[ALLERGEN-WARN] User {str(current_user.id)[:8]} is logging a food "
                    f"containing allergens they are allergic to: {sorted(offending)}. "
                    "Override header X-Allergen-Override: true was present — proceeding."
                )
    # --- End Allergen Interceptor ---

    try:
        result = await nutrition_service.create_diary_log(
            db=db,
            user_id=current_user.id,
            payload=payload,
        )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@router.get("/diary", response_model=list[DiaryLogResponse])
async def get_diary_timeline(
    target_date: date = Query(..., description="Target date (YYYY-MM-DD)"),
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get daily diary timeline with nested food/measure context."""
    logger.info(
        f"[API] GET /nutrition/diary — user: {str(current_user.id)[:8]}, date: {target_date}"
    )

    return await nutrition_service.get_daily_diary(
        db=db,
        user_id=current_user.id,
        target_date=target_date,
    )


@router.delete("/log/{log_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_nutrition_log(
    log_id: uuid.UUID,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deletes a nutrition log entry owned by the authenticated user."""
    deleted = await nutrition_service.delete_nutrition_log(
        db=db,
        user_id=current_user.id,
        log_id=log_id,
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nutrition log not found or not owned by user",
        )


# =============================================================
# Water Tracking
# =============================================================


class WaterLogRequest(BaseModel):
    date: date
    amount_ml: int = Field(gt=0, le=5000)


@router.post("/water")
async def log_water(
    payload: WaterLogRequest,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Atomically increments water intake for the target date."""
    user_id = current_user.id

    stmt = select(DailyNutritionSummary).where(
        and_(
            DailyNutritionSummary.user_id == user_id,
            DailyNutritionSummary.date == payload.date,
        )
    )
    result = await db.execute(stmt)
    summary = result.scalar_one_or_none()

    if summary:
        summary.total_water_ml = (summary.total_water_ml or 0) + payload.amount_ml
    else:
        summary = DailyNutritionSummary(
            user_id=user_id,
            date=payload.date,
            total_water_ml=payload.amount_ml,
        )
        db.add(summary)

    await db.commit()
    await db.refresh(summary)

    return {
        "user_id": str(summary.user_id),
        "date": summary.date.isoformat(),
        "total_water_ml": summary.total_water_ml,
    }


@router.get("/summary/{target_date}")
async def get_nutrition_summary(
    target_date: date,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns the daily nutrition summary for a specific date."""
    stmt = select(DailyNutritionSummary).where(
        and_(
            DailyNutritionSummary.user_id == current_user.id,
            DailyNutritionSummary.date == target_date,
        )
    )
    result = await db.execute(stmt)
    summary = result.scalar_one_or_none()

    if not summary:
        return {
            "user_id": str(current_user.id),
            "date": target_date.isoformat(),
            "total_water_ml": 0,
            "total_calories": 0,
            "total_protein": 0,
            "total_carbs": 0,
            "total_fat": 0,
        }

    return {
        "user_id": str(summary.user_id),
        "date": summary.date.isoformat(),
        "total_water_ml": summary.total_water_ml or 0,
        "total_calories": float(summary.total_calories) if summary.total_calories else 0,
        "total_protein": float(summary.total_protein) if summary.total_protein else 0,
        "total_carbs": float(summary.total_carbs) if summary.total_carbs else 0,
        "total_fat": float(summary.total_fat) if summary.total_fat else 0,
    }


# =============================================================
# Recipes
# =============================================================


class RecipeIngredientInput(BaseModel):
    food_id: uuid.UUID
    measure_id: uuid.UUID
    quantity: float = Field(gt=0)


class RecipeCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    instructions: str | None = None
    portions: int = Field(ge=1, default=1)
    ingredients: list[RecipeIngredientInput] = Field(min_length=1)


@router.post("/recipe", status_code=status.HTTP_201_CREATED)
async def create_recipe(
    payload: RecipeCreateRequest,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    V2.5 Recipe Creation — Normalizes recipe into a unified food entry.

    1. Resolves each ingredient's food + measure to compute base weight in g/ml.
    2. Calculates total weight and total absolute macros for the recipe.
    3. Normalizes macros per 100g.
    4. Creates a record in `foods` table (is_custom=True) with normalized macros.
    5. Inserts default measures: 'g' (factor=1) and 'serving' (factor=total_weight/portions).
    6. Saves raw ingredient associations in `recipe_ingredients` for audit.
    """

    # Step 1: Resolve ingredients and compute totals
    total_weight = 0.0
    total_calories = 0.0
    total_protein = 0.0
    total_carbs = 0.0
    total_fat = 0.0
    resolved_ingredients: list[tuple[uuid.UUID, float]] = []  # (food_id, weight_g)

    for ingredient in payload.ingredients:
        food = await db.get(Food, ingredient.food_id)
        if not food:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Food not found: {ingredient.food_id}",
            )

        measure = await db.get(FoodMeasure, ingredient.measure_id)
        if not measure:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Measure not found: {ingredient.measure_id}",
            )

        if measure.food_id != food.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Measure {ingredient.measure_id} does not belong to food {ingredient.food_id}",
            )

        # Calculate base weight for this ingredient
        base_weight = ingredient.quantity * float(measure.conversion_factor)
        resolved_ingredients.append((food.id, base_weight))

        # Accumulate absolute macros
        ratio = base_weight / 100.0
        total_calories += ratio * float(food.calories_per_100)
        total_protein += ratio * float(food.protein_per_100)
        total_carbs += ratio * float(food.carbs_per_100)
        total_fat += ratio * float(food.fat_per_100)
        total_weight += base_weight

    if total_weight <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Total recipe weight must be greater than 0",
        )

    # Step 2: Normalize macros per 100g
    calories_per_100 = round((total_calories / total_weight) * 100, 2)
    protein_per_100 = round((total_protein / total_weight) * 100, 2)
    carbs_per_100 = round((total_carbs / total_weight) * 100, 2)
    fat_per_100 = round((total_fat / total_weight) * 100, 2)

    # Step 3: Create unified food record for the recipe
    food_id = uuid.uuid4()
    recipe_food = Food(
        id=food_id,
        name=payload.name.strip(),
        brand=None,
        barcode=None,
        base_unit="g",
        calories_per_100=calories_per_100,
        protein_per_100=protein_per_100,
        carbs_per_100=carbs_per_100,
        fat_per_100=fat_per_100,
        is_custom=True,
        is_verified=False,
        created_by=current_user.id,
    )
    db.add(recipe_food)

    # Step 4: Insert default measures
    # 'g' measure: conversion_factor = 1.0
    g_measure_id = uuid.uuid4()
    db.add(FoodMeasure(
        id=g_measure_id,
        food_id=food_id,
        measure_name="g",
        conversion_factor=1.0,
        is_default=False,
    ))

    # 'serving' measure: conversion_factor = total_weight / portions
    serving_factor = round(total_weight / payload.portions, 4)
    serving_measure_id = uuid.uuid4()
    db.add(FoodMeasure(
        id=serving_measure_id,
        food_id=food_id,
        measure_name="serving",
        conversion_factor=serving_factor,
        is_default=True,
    ))

    # Step 5: Save recipe record for audit/history
    recipe_id = uuid.uuid4()
    recipe = Recipe(
        id=recipe_id,
        user_id=current_user.id,
        title=payload.name.strip(),
        instructions=payload.instructions,
    )
    db.add(recipe)

    # Step 6: Save ingredient associations in recipe_ingredients
    for ing_food_id, weight_g in resolved_ingredients:
        db.add(RecipeIngredient(
            recipe_id=recipe_id,
            food_id=ing_food_id,
            weight_g=round(weight_g, 2),
        ))

    await db.commit()
    # Refresh the recipe_food row to pick up DB-trigger-propagated allergens
    # (trg_propagate_recipe_allergens fires on recipe_ingredients INSERT)
    await db.refresh(recipe_food)

    return {
        "id": str(recipe_id),
        "food_id": str(food_id),
        "user_id": str(current_user.id),
        "name": payload.name.strip(),
        "instructions": payload.instructions,
        "portions": payload.portions,
        "total_weight_g": round(total_weight, 2),
        "calories_per_100": calories_per_100,
        "protein_per_100": protein_per_100,
        "carbs_per_100": carbs_per_100,
        "fat_per_100": fat_per_100,
        "allergens": recipe_food.allergens or [],
        "measures": [
            {"id": str(g_measure_id), "measure_name": "g", "conversion_factor": 1.0, "is_default": False},
            {"id": str(serving_measure_id), "measure_name": "serving", "conversion_factor": serving_factor, "is_default": True},
        ],
        "ingredients": [
            {"food_id": str(food_id), "weight_g": weight_g}
            for food_id, weight_g in resolved_ingredients
        ],
    }


@router.delete("/recipe/{recipe_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recipe(
    recipe_id: uuid.UUID,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Soft-delete a recipe owned by the authenticated user.

    Sets `is_archived = True` on the associated `foods` row to preserve
    historical log integrity. Cleans up `recipe_ingredients` rows since
    editing history of deleted recipes is unnecessary.
    """
    from sqlalchemy import delete as sql_delete
    from sqlalchemy.orm import selectinload

    # Find the recipe
    stmt = select(Recipe).where(and_(Recipe.id == recipe_id, Recipe.user_id == current_user.id))
    result = await db.execute(stmt)
    recipe = result.scalar_one_or_none()

    if not recipe:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found")

    # Find the associated food entry and soft-delete it
    food_stmt = select(Food).where(
        and_(
            Food.created_by == current_user.id,
            Food.is_custom == True,
            Food.name == recipe.title,
            Food.is_archived == False,
        )
    )
    food_result = await db.execute(food_stmt)
    food_entry = food_result.scalar_one_or_none()

    if food_entry:
        food_entry.is_archived = True

    # Clean up ingredient associations (free up space, parent food row remains)
    await db.execute(sql_delete(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id))

    # Delete the recipe audit row itself (the food row is preserved via soft-delete)
    await db.execute(sql_delete(Recipe).where(Recipe.id == recipe_id))

    await db.commit()


@router.put("/recipe/{recipe_id}")
async def update_recipe(
    recipe_id: uuid.UUID,
    payload: RecipeCreateRequest,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    V2.5 Recipe Update — Atomically recalculates macro composition.

    1. Validates the target recipe exists and is not archived.
    2. Flushes all existing recipe_ingredients for this recipe.
    3. Resolves each new ingredient's food + measure to compute base weight.
    4. Recalculates total weight and normalized macros per 100g.
    5. Updates the parent `foods` row with new macro profile and metadata.
    6. Updates the 'serving' measure with new conversion factor (total_weight / portions).
    7. Writes newly mapped ingredients into recipe_ingredients.
    8. Returns the updated food response.
    """
    from sqlalchemy import delete as sql_delete
    from sqlalchemy.orm import selectinload

    # Find the recipe owned by this user
    stmt = select(Recipe).where(and_(Recipe.id == recipe_id, Recipe.user_id == current_user.id))
    result = await db.execute(stmt)
    recipe = result.scalar_one_or_none()

    if not recipe:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found")

    # Find the associated food entry — must NOT be archived
    food_stmt = select(Food).where(
        and_(
            Food.created_by == current_user.id,
            Food.is_custom == True,
            Food.name == recipe.title,
            Food.is_archived == False,
        )
    ).options(selectinload(Food.measures))
    food_result = await db.execute(food_stmt)
    food_entry = food_result.scalar_one_or_none()

    if not food_entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Associated food entry not found or has been archived",
        )

    # Step 1: Resolve new ingredients and compute totals
    total_weight = 0.0
    total_calories = 0.0
    total_protein = 0.0
    total_carbs = 0.0
    total_fat = 0.0
    resolved_ingredients: list[tuple[uuid.UUID, float]] = []

    for ingredient in payload.ingredients:
        food = await db.get(Food, ingredient.food_id)
        if not food:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Food not found: {ingredient.food_id}",
            )

        measure = await db.get(FoodMeasure, ingredient.measure_id)
        if not measure:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Measure not found: {ingredient.measure_id}",
            )

        if measure.food_id != food.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Measure {ingredient.measure_id} does not belong to food {ingredient.food_id}",
            )

        base_weight = ingredient.quantity * float(measure.conversion_factor)
        resolved_ingredients.append((food.id, base_weight))

        ratio = base_weight / 100.0
        total_calories += ratio * float(food.calories_per_100)
        total_protein += ratio * float(food.protein_per_100)
        total_carbs += ratio * float(food.carbs_per_100)
        total_fat += ratio * float(food.fat_per_100)
        total_weight += base_weight

    if total_weight <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Total recipe weight must be greater than 0",
        )

    # Step 2: Normalize macros per 100g
    calories_per_100 = round((total_calories / total_weight) * 100, 2)
    protein_per_100 = round((total_protein / total_weight) * 100, 2)
    carbs_per_100 = round((total_carbs / total_weight) * 100, 2)
    fat_per_100 = round((total_fat / total_weight) * 100, 2)

    # Step 3: Update the parent food entry
    food_entry.name = payload.name.strip()
    food_entry.calories_per_100 = calories_per_100
    food_entry.protein_per_100 = protein_per_100
    food_entry.carbs_per_100 = carbs_per_100
    food_entry.fat_per_100 = fat_per_100

    # Step 4: Update the 'serving' measure with new conversion factor
    serving_factor = round(total_weight / payload.portions, 4)
    serving_measure = next((m for m in food_entry.measures if m.measure_name == "serving"), None)
    if serving_measure:
        serving_measure.conversion_factor = serving_factor
    else:
        # Create serving measure if it doesn't exist
        db.add(FoodMeasure(
            id=uuid.uuid4(),
            food_id=food_entry.id,
            measure_name="serving",
            conversion_factor=serving_factor,
            is_default=True,
        ))

    # Step 5: Update recipe metadata
    recipe.title = payload.name.strip()
    recipe.instructions = payload.instructions

    # Step 6: Flush existing recipe_ingredients and write new ones
    await db.execute(sql_delete(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id))

    for ing_food_id, weight_g in resolved_ingredients:
        db.add(RecipeIngredient(
            recipe_id=recipe_id,
            food_id=ing_food_id,
            weight_g=round(weight_g, 2),
        ))

    await db.commit()

    # Reload measures after commit
    await db.refresh(food_entry)
    refreshed_stmt = select(Food).where(Food.id == food_entry.id).options(selectinload(Food.measures))
    refreshed_result = await db.execute(refreshed_stmt)
    refreshed_food = refreshed_result.scalar_one()

    return {
        "id": str(recipe_id),
        "food_id": str(refreshed_food.id),
        "user_id": str(current_user.id),
        "name": payload.name.strip(),
        "instructions": payload.instructions,
        "portions": payload.portions,
        "total_weight_g": round(total_weight, 2),
        "calories_per_100": calories_per_100,
        "protein_per_100": protein_per_100,
        "carbs_per_100": carbs_per_100,
        "fat_per_100": fat_per_100,
        "measures": [
            {
                "id": str(m.id),
                "measure_name": m.measure_name,
                "conversion_factor": float(m.conversion_factor),
                "is_default": m.is_default,
            }
            for m in refreshed_food.measures
        ],
        "ingredients": [
            {"food_id": str(food_id), "weight_g": weight_g}
            for food_id, weight_g in resolved_ingredients
        ],
    }


@router.get("/recipes")
async def list_user_recipes(
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns a lightweight list of recipes for the authenticated user. Excludes archived."""
    from sqlalchemy.orm import selectinload

    stmt = (
        select(Recipe)
        .where(Recipe.user_id == current_user.id)
        .order_by(Recipe.created_at.desc())
    )
    result = await db.execute(stmt)
    recipes = result.scalars().all()

    # Look up the associated food entries (matched by title + user, non-archived)
    recipe_titles = [r.title for r in recipes]
    if not recipe_titles:
        return []

    food_stmt = (
        select(Food)
        .where(
            and_(
                Food.created_by == current_user.id,
                Food.is_custom == True,
                Food.is_archived == False,
                Food.name.in_(recipe_titles),
            )
        )
        .options(selectinload(Food.measures))
    )
    food_result = await db.execute(food_stmt)
    foods_by_name: dict[str, Food] = {}
    for f in food_result.scalars().all():
        foods_by_name[f.name] = f

    response = []
    for r in recipes:
        food_entry = foods_by_name.get(r.title)
        if not food_entry:
            continue

        recipe_data: dict = {
            "id": str(r.id),
            "title": r.title,
            "food_id": str(food_entry.id),
            "created_at": r.created_at.isoformat(),
            "calories_per_100": float(food_entry.calories_per_100),
            "protein_per_100": float(food_entry.protein_per_100),
            "carbs_per_100": float(food_entry.carbs_per_100),
            "fat_per_100": float(food_entry.fat_per_100),
            "measures": [
                {
                    "id": str(m.id),
                    "measure_name": m.measure_name,
                    "conversion_factor": float(m.conversion_factor),
                    "is_default": m.is_default,
                }
                for m in food_entry.measures
            ],
        }

        # Compute total recipe macros for display
        serving_measure = next((m for m in food_entry.measures if m.is_default), None)
        if serving_measure:
            factor = float(serving_measure.conversion_factor)
            recipe_data["total_calories"] = round(factor / 100 * float(food_entry.calories_per_100), 1)
            recipe_data["total_protein"] = round(factor / 100 * float(food_entry.protein_per_100), 1)
            recipe_data["total_carbs"] = round(factor / 100 * float(food_entry.carbs_per_100), 1)
            recipe_data["total_fat"] = round(factor / 100 * float(food_entry.fat_per_100), 1)
            recipe_data["total_weight_g"] = round(factor, 1)

        response.append(recipe_data)

    return response


@router.get("/recipe/{recipe_id}")
async def get_recipe_detail(
    recipe_id: uuid.UUID,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns full recipe detail with resolved ingredients.

    Each ingredient includes the complete Food object (with measures) so the
    frontend can render the editable view without additional API calls.
    """
    from sqlalchemy.orm import selectinload

    # Fetch recipe with ingredients
    stmt = (
        select(Recipe)
        .where(and_(Recipe.id == recipe_id, Recipe.user_id == current_user.id))
        .options(selectinload(Recipe.ingredients))
    )
    result = await db.execute(stmt)
    recipe = result.scalar_one_or_none()

    if not recipe:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found")

    # Look up the associated food entry for macro data
    food_stmt = (
        select(Food)
        .where(
            and_(
                Food.created_by == current_user.id,
                Food.is_custom == True,
                Food.is_archived == False,
                Food.name == recipe.title,
            )
        )
        .options(selectinload(Food.measures))
    )
    food_result = await db.execute(food_stmt)
    food_entry = food_result.scalar_one_or_none()

    if not food_entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe food entry not found or archived")

    # Resolve each ingredient's food with measures
    ingredient_food_ids = [ing.food_id for ing in (recipe.ingredients or [])]
    resolved_foods: dict[uuid.UUID, Food] = {}
    if ingredient_food_ids:
        ing_food_stmt = (
            select(Food)
            .where(and_(Food.id.in_(ingredient_food_ids), Food.is_archived == False))
            .options(selectinload(Food.measures))
        )
        ing_food_result = await db.execute(ing_food_stmt)
        for f in ing_food_result.scalars().all():
            resolved_foods[f.id] = f

    # Build response
    ingredients_response = []
    for ing in (recipe.ingredients or []):
        food = resolved_foods.get(ing.food_id)
        if not food:
            continue
        ingredients_response.append({
            "food_id": str(ing.food_id),
            "weight_g": float(ing.weight_g),
            "food": {
                "id": str(food.id),
                "name": food.name,
                "brand": food.brand,
                "base_unit": food.base_unit,
                "calories_per_100": float(food.calories_per_100),
                "protein_per_100": float(food.protein_per_100),
                "carbs_per_100": float(food.carbs_per_100),
                "fat_per_100": float(food.fat_per_100),
                "is_custom": food.is_custom,
                "is_verified": food.is_verified,
                "measures": [
                    {
                        "id": str(m.id),
                        "food_id": str(m.food_id),
                        "measure_name": m.measure_name,
                        "conversion_factor": float(m.conversion_factor),
                        "is_default": m.is_default,
                    }
                    for m in food.measures
                ],
            },
        })

    # Compute totals
    serving_measure = next((m for m in food_entry.measures if m.is_default), None)
    total_weight_g = float(serving_measure.conversion_factor) if serving_measure else 0
    portions = 1
    if serving_measure and total_weight_g > 0:
        # Infer portions from (total_weight / serving_factor) — but serving IS per-portion
        # So total_weight = serving_factor * portions → portions = total_weight / serving_factor = 1
        # Actually, serving_factor = total_weight / portions was set at creation time
        # We can't reverse this perfectly without storing portions, so we report serving_factor directly
        portions = 1  # User can adjust in the UI

    return {
        "id": str(recipe.id),
        "title": recipe.title,
        "instructions": recipe.instructions,
        "food_id": str(food_entry.id),
        "calories_per_100": float(food_entry.calories_per_100),
        "protein_per_100": float(food_entry.protein_per_100),
        "carbs_per_100": float(food_entry.carbs_per_100),
        "fat_per_100": float(food_entry.fat_per_100),
        "measures": [
            {
                "id": str(m.id),
                "measure_name": m.measure_name,
                "conversion_factor": float(m.conversion_factor),
                "is_default": m.is_default,
            }
            for m in food_entry.measures
        ],
        "total_calories": round(total_weight_g / 100 * float(food_entry.calories_per_100), 1) if total_weight_g else 0,
        "total_protein": round(total_weight_g / 100 * float(food_entry.protein_per_100), 1) if total_weight_g else 0,
        "total_carbs": round(total_weight_g / 100 * float(food_entry.carbs_per_100), 1) if total_weight_g else 0,
        "total_fat": round(total_weight_g / 100 * float(food_entry.fat_per_100), 1) if total_weight_g else 0,
        "total_weight_g": round(total_weight_g, 1),
        "ingredients": ingredients_response,
        "created_at": recipe.created_at.isoformat(),
    }


# =============================================================
# Food Search & Barcode
# =============================================================


@router.get("/food/search")
async def search_food_catalog(
    q: str = Query(min_length=2, max_length=100),
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Searches the unified foods table by name (case-insensitive partial match). Excludes archived foods."""
    from sqlalchemy.orm import selectinload

    stmt = (
        select(Food)
        .where(and_(Food.name.ilike(f"%{q}%"), Food.is_archived == False))
        .options(selectinload(Food.measures))
        .order_by(Food.is_verified.desc(), Food.name)
        .limit(20)
    )
    result = await db.execute(stmt)
    foods = result.scalars().all()

    return [FoodResponse.model_validate(f) for f in foods]


@router.get("/barcode/{code}")
async def lookup_barcode(
    code: str,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Database-first barcode lookup from unified foods table. Excludes archived foods."""
    from sqlalchemy.orm import selectinload

    stmt = select(Food).where(and_(Food.barcode == code, Food.is_archived == False)).options(selectinload(Food.measures))
    result = await db.execute(stmt)
    food = result.scalar_one_or_none()

    if not food:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Barcode not found in database")

    return FoodResponse.model_validate(food)

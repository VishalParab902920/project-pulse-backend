"""
Project Pulse V2 — Nutrition Router
Endpoints for diary retrieval, nutrition logging, water tracking, and log management.

Prefix: /api/v2/nutrition
"""

import logging
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.nutrition import DailyNutritionSummary, Recipe, RecipeIngredient
from app.routers.dependencies import get_current_user
from app.schemas.identity import ProfileResponse
from app.schemas.nutrition import (
    NutritionLogCreate,
    NutritionLogResponse,
)
from app.services.nutrition import nutrition_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v2/nutrition",
    tags=["Nutrition"],
)


class WaterLogRequest(BaseModel):
    """Payload for logging water intake."""
    date: date
    amount_ml: int = Field(gt=0, le=5000)


class RecipeIngredientInput(BaseModel):
    """Single ingredient in a recipe creation payload."""
    food_id: uuid.UUID
    weight_g: float = Field(gt=0)


class RecipeCreateRequest(BaseModel):
    """Payload for creating a custom recipe."""
    title: str = Field(min_length=1, max_length=255)
    instructions: str | None = None
    ingredients: list[RecipeIngredientInput] = Field(min_length=1)


@router.get("/diary", response_model=dict[str, list[NutritionLogResponse]])
async def get_daily_diary(
    target_date: date = Query(
        ...,
        description="Target date for diary retrieval (YYYY-MM-DD)",
    ),
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieves the user's nutrition diary for a specific date.

    Returns entries grouped by meal type:
    - breakfast: Morning meal entries
    - lunch: Midday meal entries
    - dinner: Evening meal entries
    - snack: Snack entries

    Each entry includes nested food/recipe details with macro information.
    """
    logger.info(
        f"[API] GET /nutrition/diary — user: {str(current_user.id)[:8]}, "
        f"date: {target_date}"
    )

    diary = await nutrition_service.get_daily_diary(
        db=db,
        user_id=current_user.id,
        target_date=target_date,
    )

    return diary


@router.post("/log", response_model=NutritionLogResponse, status_code=status.HTTP_201_CREATED)
async def log_nutrition(
    log_data: NutritionLogCreate,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Creates a new nutrition log entry for the authenticated user.

    The entry must reference either a food_id or recipe_id (or both can be null
    for manual entries). The serving_size_g determines macro calculations.
    """
    logger.info(
        f"[API] POST /nutrition/log — user: {str(current_user.id)[:8]}, "
        f"meal: {log_data.meal_type}"
    )

    try:
        result = await nutrition_service.log_nutrition(
            db=db,
            user_id=current_user.id,
            log_data=log_data,
        )
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.delete("/log/{log_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_nutrition_log(
    log_id: uuid.UUID,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Deletes a nutrition log entry owned by the authenticated user.

    Returns 204 No Content on success, 404 if the log doesn't exist
    or isn't owned by the user.
    """
    logger.info(
        f"[API] DELETE /nutrition/log/{log_id} — user: {str(current_user.id)[:8]}"
    )

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


@router.post("/water")
async def log_water(
    payload: WaterLogRequest,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Logs water intake by atomically incrementing total_water_ml
    in daily_nutrition_summaries for the target date.

    Creates the summary row if it doesn't exist.
    """
    user_id = current_user.id
    logger.info(
        f"[API] POST /nutrition/water — user: {str(user_id)[:8]}, "
        f"date: {payload.date}, amount: {payload.amount_ml}ml"
    )

    # Check if summary exists
    stmt = select(DailyNutritionSummary).where(
        and_(
            DailyNutritionSummary.user_id == user_id,
            DailyNutritionSummary.date == payload.date,
        )
    )
    result = await db.execute(stmt)
    summary = result.scalar_one_or_none()

    if summary:
        current_water = summary.total_water_ml or 0
        summary.total_water_ml = current_water + payload.amount_ml
    else:
        summary = DailyNutritionSummary(
            user_id=user_id,
            date=payload.date,
            total_calories=None,
            total_protein=None,
            total_carbs=None,
            total_fat=None,
            total_water_ml=payload.amount_ml,
        )
        db.add(summary)

    await db.commit()
    await db.refresh(summary)

    return {
        "user_id": str(summary.user_id),
        "date": summary.date.isoformat(),
        "total_water_ml": summary.total_water_ml,
        "total_calories": float(summary.total_calories) if summary.total_calories else None,
        "total_protein": float(summary.total_protein) if summary.total_protein else None,
        "total_carbs": float(summary.total_carbs) if summary.total_carbs else None,
        "total_fat": float(summary.total_fat) if summary.total_fat else None,
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


@router.post("/recipe", status_code=status.HTTP_201_CREATED)
async def create_recipe(
    payload: RecipeCreateRequest,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Creates a custom recipe with ingredients in a single transaction.

    Inserts a row into recipes and bulk-inserts all ingredients
    into recipe_ingredients mapped to the new recipe_id.
    """
    user_id = current_user.id
    logger.info(f"[API] POST /nutrition/recipe — user: {str(user_id)[:8]}, title: '{payload.title}'")

    recipe_id = uuid.uuid4()

    # Create recipe
    recipe = Recipe(
        id=recipe_id,
        user_id=user_id,
        title=payload.title,
        instructions=payload.instructions,
    )
    db.add(recipe)

    # Bulk insert ingredients
    for ingredient in payload.ingredients:
        db.add(RecipeIngredient(
            recipe_id=recipe_id,
            food_id=ingredient.food_id,
            weight_g=ingredient.weight_g,
        ))

    await db.commit()

    return {
        "id": str(recipe_id),
        "user_id": str(user_id),
        "title": payload.title,
        "instructions": payload.instructions,
        "ingredients": [
            {"food_id": str(i.food_id), "weight_g": i.weight_g}
            for i in payload.ingredients
        ],
    }


class CustomFoodCreate(BaseModel):
    """Payload for creating a custom food entry."""
    name: str = Field(min_length=1, max_length=255)
    brand: str | None = None
    calories_per_100g: float = Field(ge=0)
    protein_per_100g: float = Field(ge=0)
    carbs_per_100g: float = Field(ge=0)
    fat_per_100g: float = Field(ge=0)


@router.delete("/recipe/{recipe_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recipe(
    recipe_id: uuid.UUID,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deletes a recipe owned by the authenticated user."""
    from sqlalchemy import delete as sql_delete

    stmt = select(Recipe).where(
        and_(Recipe.id == recipe_id, Recipe.user_id == current_user.id)
    )
    result = await db.execute(stmt)
    recipe = result.scalar_one_or_none()

    if not recipe:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found")

    await db.execute(sql_delete(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id))
    await db.execute(sql_delete(Recipe).where(Recipe.id == recipe_id))
    await db.commit()


@router.post("/food", status_code=status.HTTP_201_CREATED)
async def create_custom_food(
    payload: CustomFoodCreate,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Creates a custom food entry in the food dictionary linked to the user."""
    from app.models.nutrition import FoodDictionary

    food = FoodDictionary(
        id=uuid.uuid4(),
        name=payload.name.strip(),
        brand=payload.brand,
        calories_per_100g=payload.calories_per_100g,
        protein_per_100g=payload.protein_per_100g,
        carbs_per_100g=payload.carbs_per_100g,
        fat_per_100g=payload.fat_per_100g,
        is_verified=False,
        user_id=current_user.id,
    )
    db.add(food)
    await db.commit()

    return {
        "id": str(food.id),
        "name": food.name,
        "brand": food.brand,
        "calories_per_100g": payload.calories_per_100g,
        "protein_per_100g": payload.protein_per_100g,
        "carbs_per_100g": payload.carbs_per_100g,
        "fat_per_100g": payload.fat_per_100g,
    }


@router.get("/food/search")
async def search_food_catalog(
    q: str = Query(min_length=2, max_length=100),
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Searches the food dictionary by name (case-insensitive partial match)."""
    from app.models.nutrition import FoodDictionary

    stmt = (
        select(FoodDictionary)
        .where(FoodDictionary.name.ilike(f"%{q}%"))
        .limit(20)
    )
    result = await db.execute(stmt)
    foods = result.scalars().all()

    return [
        {
            "id": str(f.id),
            "name": f.name,
            "brand": f.brand,
            "calories_per_100g": float(f.calories_per_100g),
            "protein_per_100g": float(f.protein_per_100g),
            "carbs_per_100g": float(f.carbs_per_100g),
            "fat_per_100g": float(f.fat_per_100g),
        }
        for f in foods
    ]


@router.get("/recipes")
async def list_user_recipes(
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns all recipes belonging to the authenticated user."""
    from sqlalchemy.orm import selectinload

    stmt = (
        select(Recipe)
        .where(Recipe.user_id == current_user.id)
        .options(selectinload(Recipe.ingredients).selectinload(RecipeIngredient.food))
        .order_by(Recipe.created_at.desc())
    )
    result = await db.execute(stmt)
    recipes = result.scalars().all()

    return [
        {
            "id": str(r.id),
            "title": r.title,
            "instructions": r.instructions,
            "ingredients": [
                {
                    "food_id": str(ing.food_id),
                    "weight_g": float(ing.weight_g),
                    "food_name": ing.food.name if ing.food else None,
                }
                for ing in (r.ingredients or [])
            ],
            "created_at": r.created_at.isoformat(),
        }
        for r in recipes
    ]


@router.get("/barcode/{code}")
async def lookup_barcode(
    code: str,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Database-first barcode lookup.
    1. Check food_dictionary for matching barcode
    2. If not found, return 404 (frontend handles OFF fallback)
    """
    from app.models.nutrition import FoodDictionary

    stmt = select(FoodDictionary).where(FoodDictionary.barcode == code)
    result = await db.execute(stmt)
    food = result.scalar_one_or_none()

    if not food:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Barcode not found in database",
        )

    return {
        "id": str(food.id),
        "name": food.name,
        "brand": food.brand,
        "calories_per_100g": float(food.calories_per_100g),
        "protein_per_100g": float(food.protein_per_100g),
        "carbs_per_100g": float(food.carbs_per_100g),
        "fat_per_100g": float(food.fat_per_100g),
        "barcode": food.barcode,
    }

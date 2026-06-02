"""
Project Pulse V2.5 — Nutrition Router (Consolidated)
Endpoints: food CRUD, diary CRUD, water, recipes, barcode search.

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
from app.models.nutrition import (
    DailyNutritionSummary,
    Food,
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
):
    """Create a diary log entry with pre-calculated macros."""
    logger.info(
        f"[API] POST /nutrition/diary — user: {str(current_user.id)[:8]}, "
        f"meal: {payload.meal_type}, qty: {payload.quantity}"
    )

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
    weight_g: float = Field(gt=0)


class RecipeCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    instructions: str | None = None
    ingredients: list[RecipeIngredientInput] = Field(min_length=1)


@router.post("/recipe", status_code=status.HTTP_201_CREATED)
async def create_recipe(
    payload: RecipeCreateRequest,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Creates a recipe with ingredients in a single transaction."""
    recipe_id = uuid.uuid4()
    recipe = Recipe(id=recipe_id, user_id=current_user.id, title=payload.title, instructions=payload.instructions)
    db.add(recipe)

    for ingredient in payload.ingredients:
        db.add(RecipeIngredient(recipe_id=recipe_id, food_id=ingredient.food_id, weight_g=ingredient.weight_g))

    await db.commit()
    return {
        "id": str(recipe_id),
        "user_id": str(current_user.id),
        "title": payload.title,
        "instructions": payload.instructions,
        "ingredients": [{"food_id": str(i.food_id), "weight_g": i.weight_g} for i in payload.ingredients],
    }


@router.delete("/recipe/{recipe_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recipe(
    recipe_id: uuid.UUID,
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deletes a recipe owned by the authenticated user."""
    from sqlalchemy import delete as sql_delete

    stmt = select(Recipe).where(and_(Recipe.id == recipe_id, Recipe.user_id == current_user.id))
    result = await db.execute(stmt)
    recipe = result.scalar_one_or_none()

    if not recipe:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recipe not found")

    await db.execute(sql_delete(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe_id))
    await db.execute(sql_delete(Recipe).where(Recipe.id == recipe_id))
    await db.commit()


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
                {"food_id": str(ing.food_id), "weight_g": float(ing.weight_g), "food_name": ing.food.name if ing.food else None}
                for ing in (r.ingredients or [])
            ],
            "created_at": r.created_at.isoformat(),
        }
        for r in recipes
    ]


# =============================================================
# Food Search & Barcode
# =============================================================


@router.get("/food/search")
async def search_food_catalog(
    q: str = Query(min_length=2, max_length=100),
    current_user: ProfileResponse = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Searches the unified foods table by name (case-insensitive partial match)."""
    from sqlalchemy.orm import selectinload

    stmt = (
        select(Food)
        .where(Food.name.ilike(f"%{q}%"))
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
    """Database-first barcode lookup from unified foods table."""
    from sqlalchemy.orm import selectinload

    stmt = select(Food).where(Food.barcode == code).options(selectinload(Food.measures))
    result = await db.execute(stmt)
    food = result.scalar_one_or_none()

    if not food:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Barcode not found in database")

    return FoodResponse.model_validate(food)

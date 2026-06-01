"""
Project Pulse — Automated Programmatic Ingestion & Seeding Engine
=================================================================
Fetches real-world exercise data from Wger API and Indian food data from
IFCT 2017 (Indian Food Composition Tables), curated dishes, and branded
items to seed the database idempotently.

Usage:
    python run_seed.py --test          # Seed 50 exercises + 50 foods
    python run_seed.py --full          # Seed complete datasets
    python run_seed.py --reset --full  # Truncate tables first, then full seed
"""

import argparse
import asyncio
import csv
import io
import json
import logging
import os
import re
import sys
import uuid
from pathlib import Path
from uuid import NAMESPACE_DNS, uuid5

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, engine

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[SEED] %(asctime)s — %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("seed")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Wger exerciseinfo endpoint includes translations (names) inline
WGER_EXERCISE_INFO_URL = "https://wger.de/api/v2/exerciseinfo/?format=json&limit=100"

# IFCT 2017 — Indian Food Composition Tables (542 raw ingredients, per-100g)
IFCT_CSV_URL = "https://raw.githubusercontent.com/ifct2017/compositions/master/compositions/index.csv"

# Local data files
DATA_DIR = Path(__file__).parent / "data"
INDIAN_DISHES_FILE = DATA_DIR / "indian_dishes.json"
INDIAN_BRANDS_FILE = DATA_DIR / "indian_brands.json"

# Wger muscle group ID → our clean category string
MUSCLE_GROUP_MAP: dict[int, str] = {
    1: "Biceps",
    2: "Shoulders",
    3: "Chest",
    4: "Back",
    5: "Triceps",
    8: "Glutes",
    9: "Quads",
    10: "Hamstrings",
    11: "Calves",
    14: "Core",
}

# Wger exercise category ID → our strict category enum
CATEGORY_MAP: dict[int, str] = {
    8: "cardio",
    9: "strength",    # Abs
    10: "strength",   # Stretching
    11: "strength",   # Arms
    12: "strength",   # Back
    13: "strength",   # Calves
    14: "strength",   # Chest
    15: "strength",   # Legs
    16: "strength",   # Shoulders
}

# Retry configuration
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0  # seconds

# Energy conversion: IFCT stores energy in kJ, we need kcal
KJ_TO_KCAL = 4.184


# ---------------------------------------------------------------------------
# Deterministic UUID Helper
# ---------------------------------------------------------------------------
def get_uuid(namespace: str, name: str) -> str:
    """
    Generate a deterministic UUID5 from a namespace string and a name.
    Ensures that 'Egg' or 'Bench Press' always maps to the same UUID
    across every run.
    """
    ns = uuid5(NAMESPACE_DNS, namespace)
    return str(uuid5(ns, name))


# ---------------------------------------------------------------------------
# Utility: HTML tag stripper
# ---------------------------------------------------------------------------
def strip_html(text_value: str) -> str:
    """Remove HTML tags from a string."""
    if not text_value:
        return ""
    return re.sub(r"<[^>]*>", "", text_value).strip()


# ---------------------------------------------------------------------------
# Utility: Retry-enabled HTTP GET
# ---------------------------------------------------------------------------
async def fetch_with_retry(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """
    Perform an HTTP GET with exponential backoff retry on failure.
    Raises after MAX_RETRIES exhausted.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await client.get(url, timeout=30.0)
            response.raise_for_status()
            return response
        except (httpx.HTTPStatusError, httpx.RequestError, httpx.TimeoutException) as exc:
            if attempt == MAX_RETRIES:
                logger.error("Failed to fetch %s after %d attempts: %s", url, MAX_RETRIES, exc)
                raise
            wait = RETRY_BACKOFF_BASE ** attempt
            logger.warning(
                "Attempt %d/%d failed for %s — retrying in %.1fs: %s",
                attempt,
                MAX_RETRIES,
                url,
                wait,
                exc,
            )
            await asyncio.sleep(wait)
    # Should never reach here, but satisfy type checker
    raise RuntimeError("Unreachable")


# ---------------------------------------------------------------------------
# Exercise Ingestion (Wger exerciseinfo API)
# ---------------------------------------------------------------------------
async def ingest_exercises(session: AsyncSession, limit: int | None = None) -> int:
    """
    Fetch exercises from the Wger exerciseinfo API (includes translations),
    extract English names, transform to our schema, and upsert into the
    exercises table.

    Returns the number of rows inserted.
    """
    logger.info("Fetching exercises from Wger API...")

    exercises_processed: list[dict] = []

    async with httpx.AsyncClient() as client:
        url: str | None = WGER_EXERCISE_INFO_URL

        while url:
            response = await fetch_with_retry(client, url)
            data = response.json()

            for ex in data.get("results", []):
                # Extract English name from translations (language=2 is English)
                english_name = None
                english_description = ""
                for translation in ex.get("translations", []):
                    if translation.get("language") == 2:
                        english_name = translation.get("name", "").strip()
                        english_description = translation.get("description", "")
                        break

                if not english_name:
                    continue

                # Determine category
                category_obj = ex.get("category", {})
                category_id = category_obj.get("id", 0) if isinstance(category_obj, dict) else 0
                category = CATEGORY_MAP.get(category_id, "hypertrophy")

                # Determine primary muscle group
                muscles = ex.get("muscles", [])
                primary_muscle = None
                if muscles:
                    first_muscle = muscles[0]
                    muscle_id = first_muscle.get("id") if isinstance(first_muscle, dict) else first_muscle
                    primary_muscle = MUSCLE_GROUP_MAP.get(muscle_id)

                exercises_processed.append({
                    "name": english_name,
                    "category": category,
                    "primary_muscle_group": primary_muscle,
                    "description": strip_html(english_description),
                })

                if limit and len(exercises_processed) >= limit:
                    break

            url = data.get("next")

            if limit and len(exercises_processed) >= limit:
                break

    if limit:
        exercises_processed = exercises_processed[:limit]

    logger.info("Processing %d exercises...", len(exercises_processed))

    # Deduplicate by name (keep first occurrence)
    seen_names: set[str] = set()
    unique_exercises: list[dict] = []

    for ex in exercises_processed:
        name = ex["name"]
        if name.lower() in seen_names:
            continue
        seen_names.add(name.lower())
        unique_exercises.append({
            "id": get_uuid("projectpulse.exercises", name),
            "name": name,
            "category": ex["category"],
            "primary_muscle_group": ex["primary_muscle_group"],
        })

    # Batch insert in chunks to avoid connection timeouts
    BATCH_SIZE = 50
    rows_inserted = 0
    stmt = text("""
        INSERT INTO exercises (id, name, category, primary_muscle_group, user_id, created_at, updated_at)
        VALUES (:id, :name, :category, :primary_muscle_group, NULL, NOW(), NOW())
        ON CONFLICT (id) DO NOTHING
    """)

    for i in range(0, len(unique_exercises), BATCH_SIZE):
        batch = unique_exercises[i:i + BATCH_SIZE]
        for ex in batch:
            await session.execute(stmt, ex)
        await session.flush()
        rows_inserted += len(batch)
        rows_inserted += 1

    await session.flush()
    logger.info("Exercises ingested: %d rows.", rows_inserted)
    return rows_inserted


# ---------------------------------------------------------------------------
# Food Ingestion: IFCT 2017 (Indian Food Composition Tables)
# ---------------------------------------------------------------------------
async def ingest_ifct_foods(client: httpx.AsyncClient) -> list[dict]:
    """
    Fetch the IFCT 2017 CSV from GitHub and parse into food dicts.
    Energy is in kJ — converted to kcal (÷ 4.184).
    Returns list of food dicts ready for DB insertion.
    """
    logger.info("Fetching IFCT 2017 data from GitHub...")
    response = await fetch_with_retry(client, IFCT_CSV_URL)
    csv_text = response.text

    foods: list[dict] = []
    reader = csv.DictReader(io.StringIO(csv_text))

    for row in reader:
        name = (row.get("name") or "").strip()
        if not name:
            continue

        # Parse numeric values safely
        def safe_float(val: str | None) -> float:
            if not val or val.strip() == "":
                return 0.0
            try:
                return float(val.strip())
            except (ValueError, TypeError):
                return 0.0

        energy_kj = safe_float(row.get("enerc"))
        protein = safe_float(row.get("protcnt"))
        fat = safe_float(row.get("fatce"))
        carbs = safe_float(row.get("choavldf"))

        # Convert kJ to kcal
        calories = round(energy_kj / KJ_TO_KCAL, 2)

        # Skip items with zero everything
        if calories == 0 and protein == 0 and fat == 0 and carbs == 0:
            continue

        foods.append({
            "name": name,
            "brand": None,
            "calories": calories,
            "protein": protein,
            "carbs": carbs,
            "fat": fat,
            "source": "ifct",
        })

    logger.info("IFCT 2017: parsed %d raw ingredients.", len(foods))
    return foods


# ---------------------------------------------------------------------------
# Food Ingestion: Indian Cooked Dishes (local JSON)
# ---------------------------------------------------------------------------
def load_indian_dishes() -> list[dict]:
    """
    Load curated Indian cooked dishes from local JSON file.
    Returns list of food dicts ready for DB insertion.
    """
    logger.info("Loading Indian dishes from %s...", INDIAN_DISHES_FILE)

    with open(INDIAN_DISHES_FILE, "r", encoding="utf-8") as f:
        dishes = json.load(f)

    foods: list[dict] = []
    for dish in dishes:
        foods.append({
            "name": dish["name"],
            "brand": None,
            "calories": float(dish["calories_per_100g"]),
            "protein": float(dish["protein_per_100g"]),
            "carbs": float(dish["carbs_per_100g"]),
            "fat": float(dish["fat_per_100g"]),
            "source": "dishes",
        })

    logger.info("Indian dishes: loaded %d items.", len(foods))
    return foods


# ---------------------------------------------------------------------------
# Food Ingestion: Indian Branded/Packaged Items (local JSON)
# ---------------------------------------------------------------------------
def load_indian_brands() -> list[dict]:
    """
    Load popular Indian branded food items from local JSON file.
    Returns list of food dicts ready for DB insertion.
    """
    logger.info("Loading Indian branded items from %s...", INDIAN_BRANDS_FILE)

    with open(INDIAN_BRANDS_FILE, "r", encoding="utf-8") as f:
        brands = json.load(f)

    foods: list[dict] = []
    for item in brands:
        foods.append({
            "name": item["name"],
            "brand": item.get("brand"),
            "calories": float(item["calories_per_100g"]),
            "protein": float(item["protein_per_100g"]),
            "carbs": float(item["carbs_per_100g"]),
            "fat": float(item["fat_per_100g"]),
            "source": "brands",
        })

    logger.info("Indian brands: loaded %d items.", len(foods))
    return foods


# ---------------------------------------------------------------------------
# Food Ingestion: Combined Indian Food Database
# ---------------------------------------------------------------------------
async def ingest_foods(session: AsyncSession, limit: int | None = None) -> int:
    """
    Combine all three Indian food sources (IFCT raw ingredients, cooked dishes,
    branded items), deduplicate, and upsert into the food_dictionary table.

    For --test mode: picks a balanced mix from all three sources (50 total).
    For --full mode: ingests everything from all three sources.

    Returns the number of rows inserted.
    """
    logger.info("Building Indian food database from 3 sources...")

    # 1. Fetch IFCT 2017 data from GitHub
    async with httpx.AsyncClient() as client:
        ifct_foods = await ingest_ifct_foods(client)

    # 2. Load local curated dishes
    dishes_foods = load_indian_dishes()

    # 3. Load local branded items
    brands_foods = load_indian_brands()

    # For test mode, take a balanced sample from each source
    if limit:
        # Distribute limit across sources: ~40% IFCT, ~30% dishes, ~30% brands
        ifct_limit = limit * 2 // 5       # 20 items from IFCT
        dishes_limit = limit * 3 // 10    # 15 items from dishes
        brands_limit = limit - ifct_limit - dishes_limit  # 15 items from brands

        ifct_foods = ifct_foods[:ifct_limit]
        dishes_foods = dishes_foods[:dishes_limit]
        brands_foods = brands_foods[:brands_limit]

    # Combine all sources
    all_foods = ifct_foods + dishes_foods + brands_foods

    logger.info(
        "Combined food database: %d items (IFCT: %d, Dishes: %d, Brands: %d)",
        len(all_foods),
        len(ifct_foods),
        len(dishes_foods),
        len(brands_foods),
    )

    # Deduplicate by name (keep first occurrence — IFCT takes priority)
    seen_names: set[str] = set()
    unique_foods: list[dict] = []

    for food in all_foods:
        name = food["name"]
        if name.lower() in seen_names:
            continue
        seen_names.add(name.lower())
        unique_foods.append({
            "id": get_uuid("projectpulse.foods", name),
            "name": name,
            "brand": food["brand"],
            "calories": food["calories"],
            "protein": food["protein"],
            "carbs": food["carbs"],
            "fat": food["fat"],
        })

    # Batch insert in chunks to avoid connection timeouts
    BATCH_SIZE = 50
    rows_inserted = 0
    stmt = text("""
        INSERT INTO food_dictionary
            (id, name, brand, calories_per_100g, protein_per_100g, carbs_per_100g,
             fat_per_100g, is_verified, barcode, user_id, created_at, updated_at)
        VALUES
            (:id, :name, :brand, :calories, :protein, :carbs, :fat,
             TRUE, NULL, NULL, NOW(), NOW())
        ON CONFLICT (id) DO NOTHING
    """)

    for i in range(0, len(unique_foods), BATCH_SIZE):
        batch = unique_foods[i:i + BATCH_SIZE]
        for food in batch:
            await session.execute(stmt, food)
        await session.flush()
        rows_inserted += len(batch)

    logger.info("Foods ingested: %d rows.", rows_inserted)
    return rows_inserted


# ---------------------------------------------------------------------------
# Reset (Truncate) Tables
# ---------------------------------------------------------------------------
async def reset_tables(session: AsyncSession) -> None:
    """Truncate exercises and food_dictionary tables with CASCADE."""
    logger.warning("Resetting tables: TRUNCATE exercises, food_dictionary CASCADE")
    await session.execute(text("TRUNCATE TABLE exercises, food_dictionary CASCADE;"))
    await session.flush()
    logger.info("Tables truncated successfully.")


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------
async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Project Pulse — Automated Programmatic Ingestion & Seeding Engine"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Truncate target tables before seeding.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test mode: ingest exactly 50 exercises and 50 foods.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Full mode: ingest the complete public datasets.",
    )

    args = parser.parse_args()

    if not args.test and not args.full:
        parser.error("You must specify either --test or --full.")

    # Determine limits
    exercise_limit: int | None = 50 if args.test else None
    food_limit: int | None = 50 if args.test else None  # None = all foods from all sources

    if args.test:
        logger.info("Test mode active. Ingesting 50 exercises + 50 foods (mixed sources)...")
    else:
        logger.info("Full mode active. Ingesting all exercises + all Indian foods...")

    # Suppress SQLAlchemy echo during bulk seed to reduce noise
    engine.echo = False
    async with async_session() as session:
        # Use auto-commit mode with explicit commits per batch
        # to avoid Supabase connection timeouts on long transactions
        if args.reset:
            async with session.begin():
                await reset_tables(session)

        async with session.begin():
            total_exercises = await ingest_exercises(session, limit=exercise_limit)

        async with session.begin():
            total_foods = await ingest_foods(session, limit=food_limit)

    # Dispose engine connections cleanly
    await engine.dispose()

    logger.info(
        "Seed complete. Exercises: %d | Foods: %d",
        total_exercises,
        total_foods,
    )


if __name__ == "__main__":
    asyncio.run(main())

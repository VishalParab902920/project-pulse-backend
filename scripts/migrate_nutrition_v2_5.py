"""
Project Pulse V2.5 — Nutrition Migration Script
Async migration script to safely transition legacy nutrition data to normalized schema.

Usage:
    python -m scripts.migrate_nutrition_v2_5

Environment:
    Requires DATABASE_URL or Supabase environment variables configured in app.config.
"""

import asyncio
import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Build connection URL
DATABASE_URL = (
    f"postgresql+asyncpg://{settings.supabase_db_user}:"
    f"{settings.supabase_db_password}@{settings.supabase_db_host}:"
    f"{settings.supabase_db_port}/{settings.supabase_db_name}"
)

engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    echo=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def migrate_nutrition_data():
    """
    Migrate legacy nutrition data to normalized V2.5 schema.
    
    Steps:
    1. Insert foods from legacy food_dictionary (default base_unit = 'g')
    2. Insert default 'g' FoodMeasure for each food (conversion_factor = 1.0)
    3. Migrate nutrition_logs to v2 with pre-calculated macro fields
    4. All operations run in a single atomic transaction
    """
    async with AsyncSessionLocal() as session:
        async with session.begin():
            logger.info("Starting nutrition data migration...")
            
            # Step 1: Migrate foods from legacy food_dictionary
            logger.info("Step 1: Migrating foods from food_dictionary...")
            result = await session.execute(text("""
                INSERT INTO foods (
                    id, name, brand, barcode, base_unit,
                    calories_per_100, protein_per_100, carbs_per_100, fat_per_100,
                    is_custom, created_by, created_at
                )
                SELECT 
                    id,
                    name,
                    brand,
                    barcode,
                    'g',  -- Default base_unit
                    COALESCE(calories_per_100g, 0)::numeric(10,2),
                    COALESCE(protein_per_100g, 0)::numeric(10,2),
                    COALESCE(carbs_per_100g, 0)::numeric(10,2),
                    COALESCE(fat_per_100g, 0)::numeric(10,2),
                    COALESCE(user_id IS NOT NULL, false),
                    user_id,
                    COALESCE(created_at, :now)
                FROM food_dictionary
                ON CONFLICT DO NOTHING
                RETURNING id, name
            """), {"now": datetime.utcnow()})
            
            foods_inserted = result.rowcount if result else 0
            logger.info(f"Inserted {foods_inserted} foods (may include conflicts)")
            
            # Step 2: Insert default 'g' measure for each food
            logger.info("Step 2: Inserting default 'g' measures...")
            result = await session.execute(text("""
                INSERT INTO food_measures (food_id, measure_name, conversion_factor, is_default)
                SELECT 
                    id, 
                    'g', 
                    1.0::numeric(10,4), 
                    true
                FROM foods
                WHERE NOT EXISTS (
                    SELECT 1 FROM food_measures 
                    WHERE food_id = foods.id AND measure_name = 'g'
                )
                RETURNING id, food_id
            """))
            
            measures_inserted = result.rowcount if result else 0
            logger.info(f"Inserted {measures_inserted} default measures")
            
            # Step 3: Migrate nutrition_logs with calculated fields
            logger.info("Step 3: Migrating nutrition_logs to v2...")
            result = await session.execute(text("""
                INSERT INTO nutrition_logs_v2 (
                    id, user_id, logged_at, meal_type, food_id, measure_id, quantity,
                    calculated_qty_base, calculated_calories, calculated_protein, 
                    calculated_carbs, calculated_fat, created_at
                )
                SELECT 
                    nl.id,
                    nl.user_id,
                    nl.logged_at,
                    nl.meal_type,
                    nl.food_id,
                    fm.id,
                    nl.serving_size_g::numeric(10,2),
                    -- Pre-calculated base quantity
                    nl.serving_size_g::numeric(10,2),
                    -- Pre-calculated calories
                    (nl.serving_size_g / 100.0) * COALESCE(fd.calories_per_100g, 0)::numeric(10,2),
                    -- Pre-calculated protein
                    (nl.serving_size_g / 100.0) * COALESCE(fd.protein_per_100g, 0)::numeric(10,2),
                    -- Pre-calculated carbs
                    (nl.serving_size_g / 100.0) * COALESCE(fd.carbs_per_100g, 0)::numeric(10,2),
                    -- Pre-calculated fat
                    (nl.serving_size_g / 100.0) * COALESCE(fd.fat_per_100g, 0)::numeric(10,2),
                    COALESCE(nl.created_at, :now)
                FROM nutrition_logs nl
                LEFT JOIN food_dictionary fd ON nl.food_id = fd.id
                LEFT JOIN food_measures fm ON 
                    fm.food_id = nl.food_id AND fm.measure_name = 'g'
                WHERE nl.food_id IS NOT NULL
                ON CONFLICT DO NOTHING
                RETURNING id
            """), {"now": datetime.utcnow()})
            
            logs_migrated = result.rowcount if result else 0
            logger.info(f"Migrated {logs_migrated} nutrition log entries")
            
            # Commit transaction
            await session.commit()
            logger.info("Migration completed successfully!")
            
            return {
                "foods_migrated": foods_inserted,
                "measures_created": measures_inserted,
                "logs_migrated": logs_migrated,
            }


async def verify_migration():
    """Verify migration results."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("""
            SELECT 
                (SELECT COUNT(*) FROM foods) as foods_count,
                (SELECT COUNT(*) FROM food_measures) as measures_count,
                (SELECT COUNT(*) FROM nutrition_logs_v2) as logs_v2_count,
                (SELECT COUNT(*) FROM nutrition_logs) as legacy_logs_count
        """))
        row = result.fetchone()
        
        logger.info("=== Migration Verification ===")
        logger.info(f"foods: {row.foods_count}")
        logger.info(f"food_measures: {row.measures_count}")
        logger.info(f"nutrition_logs_v2: {row.logs_v2_count}")
        logger.info(f"nutrition_logs (legacy): {row.legacy_logs_count}")
        
        return {
            "foods": row.foods_count,
            "measures": row.measures_count,
            "logs_v2": row.logs_v2_count,
            "legacy_logs": row.legacy_logs_count,
        }


async def rollback_migration():
    """Rollback: Drop v2 tables to restore legacy state."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            logger.warning("Rolling back migration...")
            await session.execute(text("DROP TABLE IF EXISTS nutrition_logs_v2 CASCADE"))
            await session.execute(text("DROP TABLE IF EXISTS food_measures CASCADE"))
            await session.execute(text("DROP TABLE IF EXISTS foods CASCADE"))
            await session.commit()
            logger.info("Rollback complete!")


async def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Nutrition V2.5 Migration Script")
    parser.add_argument("--verify", action="store_true", help="Verify migration only")
    parser.add_argument("--rollback", action="store_true", help="Rollback migration")
    args = parser.parse_args()
    
    try:
        if args.verify:
            await verify_migration()
        elif args.rollback:
            await rollback_migration()
        else:
            await migrate_nutrition_data()
            await verify_migration()
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
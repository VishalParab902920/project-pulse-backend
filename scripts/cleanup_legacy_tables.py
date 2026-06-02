"""
Project Pulse V2.5 — Legacy Table Cleanup Script
Drops deprecated nutrition_logs table after verifying migration is complete.

Usage:
    python -m scripts.cleanup_legacy_tables
    python -m scripts.cleanup_legacy_tables --verify
"""

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = (
    f"postgresql+asyncpg://{settings.supabase_db_user}:"
    f"{settings.supabase_db_password}@{settings.supabase_db_host}:"
    f"{settings.supabase_db_port}/{settings.supabase_db_name}"
)

engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, echo=False)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def verify_migration_complete() -> bool:
    """Verify V2.5 migration data exists before dropping legacy tables."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("""
            SELECT
                (SELECT COUNT(*) FROM foods) as foods_count,
                (SELECT COUNT(*) FROM food_measures) as measures_count,
                (SELECT COUNT(*) FROM nutrition_logs_v2) as logs_v2_count
        """))
        row = result.fetchone()

        logger.info("=== Migration Verification ===")
        logger.info(f"foods: {row.foods_count}")
        logger.info(f"food_measures: {row.measures_count}")
        logger.info(f"nutrition_logs_v2: {row.logs_v2_count}")

        if row.foods_count == 0:
            logger.error("No foods found — migration may not have run!")
            return False

        return True


async def drop_legacy_nutrition_logs():
    """Drop deprecated nutrition_logs table."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            # Check if table exists
            result = await session.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name = 'nutrition_logs'
                )
            """))
            exists = result.scalar()

            if not exists:
                logger.info("nutrition_logs table already dropped — nothing to do")
                return

            logger.warning("Dropping legacy nutrition_logs table...")
            await session.execute(text(
                "DROP TRIGGER IF EXISTS trigger_update_nutrition_log ON nutrition_logs"
            ))
            await session.execute(text("DROP TABLE IF EXISTS nutrition_logs CASCADE"))
            await session.commit()
            logger.info("Legacy nutrition_logs table dropped successfully")


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Legacy Table Cleanup")
    parser.add_argument("--verify", action="store_true", help="Only verify, don't drop")
    args = parser.parse_args()

    ok = await verify_migration_complete()

    if args.verify:
        return

    if not ok:
        logger.error("Aborting — migration verification failed")
        return

    await drop_legacy_nutrition_logs()
    logger.info("Cleanup complete!")


if __name__ == "__main__":
    asyncio.run(main())

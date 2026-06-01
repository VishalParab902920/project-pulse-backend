"""
Project Pulse V2 — Async Database Connection
SQLAlchemy 2.0 async engine and session management for Supabase PostgreSQL 15+.

Uses asyncpg driver with connection pooling optimized for production workloads.
"""

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# Build the async connection URL programmatically — handles special chars in password
database_url = URL.create(
    drivername="postgresql+asyncpg",
    username=settings.supabase_db_user,
    password=settings.supabase_db_password,
    host=settings.supabase_db_host,
    port=settings.supabase_db_port,
    database=settings.supabase_db_name,
)

# Async engine with production-grade pool settings
# statement_cache_size=0 disables asyncpg prepared statement caching,
# required for Supabase PgBouncer transaction-pool mode compatibility.
engine = create_async_engine(
    database_url,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
    echo=settings.debug,
    connect_args={
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
    },
)

# Async session factory
async_session = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(AsyncAttrs, DeclarativeBase):
    """Base class for all SQLAlchemy ORM models with async attribute support."""
    pass


async def get_db():
    """FastAPI dependency that yields an async database session."""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()

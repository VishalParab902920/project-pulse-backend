"""
Project Pulse — Database Connection
SQLAlchemy engine and session management for Supabase PostgreSQL.

Uses URL.create() to safely handle passwords containing special characters
(like @, #, %) without requiring manual URL-encoding in .env files.
"""

from sqlalchemy import create_engine, URL
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# Build the connection URL programmatically — handles special chars in password
database_url = URL.create(
    drivername="postgresql",
    username=settings.supabase_db_user,
    password=settings.supabase_db_password,
    host=settings.supabase_db_host,
    port=settings.supabase_db_port,
    database=settings.supabase_db_name,
)

engine = create_engine(
    database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""
    pass


def get_db():
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

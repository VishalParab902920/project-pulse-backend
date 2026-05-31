"""
Project Pulse — Entry ORM Model
Maps to the `entries` table in Supabase PostgreSQL.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Float, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB

from app.database import Base


class Entry(Base):
    __tablename__ = "entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False)
    type = Column(String, nullable=False)
    status = Column(String, default="pending")
    raw_input = Column(Text, nullable=True)
    media_path = Column(Text, nullable=True)
    parsed_data = Column(JSONB, nullable=False)
    confidence_score = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    occurred_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

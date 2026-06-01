"""
Project Pulse V2 — AI Reports Model
Maps: ai_reports
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AIReport(Base):
    """
    Maps to `ai_reports` table.
    Caches generated weekly AI synthesis reports to prevent re-generation.
    """

    __tablename__ = "ai_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    week_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    report_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "week_start_date", name="uq_ai_reports_user_week"),
    )

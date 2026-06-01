"""
Project Pulse V2 — Domain 5: AI & Memory Models
Maps: conversations, chat_messages, semantic_memory
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Conversation(Base):
    """
    Maps to `conversations` table.
    Context thread grouping for AI chat sessions.
    """

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=True,
    )
    started_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )

    # Relationships
    profile: Mapped["Profile"] = relationship(
        "Profile", back_populates="conversations"
    )
    messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage", back_populates="conversation", lazy="selectin", cascade="all, delete-orphan"
    )


class ChatMessage(Base):
    """
    Maps to `chat_messages` table.
    Rolling chat logs within a conversation thread.
    """

    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default="now()"
    )

    # Relationships
    conversation: Mapped["Conversation"] = relationship(
        "Conversation", back_populates="messages"
    )


class SemanticMemory(Base):
    """
    Maps to `semantic_memory` table.
    Permanent vectorized user episodic memory using text-embedding-004 (768-dim).
    HNSW index on embedding column for cosine distance similarity search.
    """

    __tablename__ = "semantic_memory"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="CASCADE"),
        nullable=True,
    )
    context_chunk: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(768), nullable=False)
    importance_weight: Mapped[int] = mapped_column(Integer, default=1)
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        nullable=True, server_default="now()"
    )
    created_at: Mapped[datetime | None] = mapped_column(
        nullable=True, server_default="now()"
    )


# Forward reference imports for type checking
from app.models.identity import Profile  # noqa: E402, F401

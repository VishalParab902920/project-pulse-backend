"""
Project Pulse — Pydantic Schemas for Entries
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class EntryResponse(BaseModel):
    """Single entry response."""
    id: UUID
    user_id: UUID
    type: str
    status: str
    raw_input: str | None
    media_path: str | None = None
    parsed_data: dict[str, Any]
    confidence_score: float | None
    created_at: str | None = None
    occurred_at: str | None = None

    class Config:
        from_attributes = True


class EntryPatchRequest(BaseModel):
    """Request body for updating an entry."""
    status: str | None = Field(None, description="New status: confirmed, assumed")
    parsed_data: dict[str, Any] | None = Field(None, description="Updated parsed data")


class EntryPatchResponse(BaseModel):
    """Response after patching an entry."""
    id: UUID
    user_id: UUID
    type: str
    status: str
    raw_input: str | None
    parsed_data: dict[str, Any]
    confidence_score: float | None

    class Config:
        from_attributes = True

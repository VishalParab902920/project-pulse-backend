"""
Project Pulse — Pydantic Schemas for the Parse Pipeline
Defines request/response models for /api/v1/parse.
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ParseRequest(BaseModel):
    """Incoming request to the parse endpoint."""
    text: str = Field(..., min_length=1, max_length=2000, description="Raw user input text")


class ParseResponse(BaseModel):
    """Response from the parse endpoint after AI processing and DB persistence."""
    id: UUID
    user_id: UUID
    type: str
    status: str
    raw_input: str | None
    media_path: str | None = None
    parsed_data: dict[str, Any]
    confidence_score: float | None
    short_persona_response: str

    class Config:
        from_attributes = True

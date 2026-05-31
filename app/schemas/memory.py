"""
Project Pulse — Pydantic Schemas for Semantic Memory
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class MemoryCreateRequest(BaseModel):
    """Request to store a new semantic memory."""
    category: str = Field(..., description="recipe, exercise, or medical_fact")
    label: str = Field(..., min_length=1, max_length=200, description="Human-readable label")
    content: dict[str, Any] = Field(..., description="Macro/set payload or structured data")


class MemoryResponse(BaseModel):
    """Response after creating or retrieving a memory."""
    id: UUID
    category: str
    label: str
    content: dict[str, Any]

    class Config:
        from_attributes = True


class MemorySearchResult(BaseModel):
    """Result from a vector similarity search."""
    label: str
    category: str
    content: dict[str, Any]
    distance: float

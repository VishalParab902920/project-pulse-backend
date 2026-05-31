"""
Project Pulse — Pydantic Schemas for BYOK
"""

from pydantic import BaseModel, Field


class BYOKSaveRequest(BaseModel):
    """Request to save a user's API key to the vault."""
    api_key: str = Field(..., min_length=10, max_length=200, description="Google AI Studio API key")


class BYOKResponse(BaseModel):
    """Response after saving/testing a key."""
    status: str
    message: str


class BYOKTestRequest(BaseModel):
    """Request to test a key before saving."""
    api_key: str = Field(..., min_length=10, max_length=200)

"""
Project Pulse — Pydantic Schemas for Profile
"""

from pydantic import BaseModel, Field


class ProfileResponse(BaseModel):
    """Profile data returned to the frontend."""
    persona_name: str
    persona_vibe: str
    persona_custom_instructions: str | None = None
    unit_preference: str
    daily_goals: dict | None = None
    subscription_tier: str
    analytics_include_assumed: bool
    onboarding_status: str

    class Config:
        from_attributes = True


class ProfilePatchRequest(BaseModel):
    """Request body for updating profile fields."""
    persona_name: str | None = Field(None, max_length=50)
    persona_vibe: str | None = Field(None, max_length=50)
    persona_custom_instructions: str | None = Field(None, max_length=500)
    unit_preference: str | None = Field(None, pattern="^(metric|imperial)$")
    daily_goals: dict | None = None
    analytics_include_assumed: bool | None = None
    onboarding_status: str | None = Field(None, pattern="^(pending|completed|complete)$")

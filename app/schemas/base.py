"""
Project Pulse V2 — Base Pydantic Schema Configuration
Provides shared configuration for all DTO schemas.
"""

from pydantic import BaseModel, ConfigDict


class BaseSchema(BaseModel):
    """
    Base schema with ORM mode enabled for automatic conversion
    from SQLAlchemy model instances to Pydantic response objects.
    """

    model_config = ConfigDict(from_attributes=True)

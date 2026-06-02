"""
Project Pulse V2 — Health Check Router
Lightweight async health endpoint for monitoring and frontend connectivity checks.

Prefix: /api/v2/health
"""

from fastapi import APIRouter

router = APIRouter(
    prefix="/api/v2/health",
    tags=["Health"],
)


@router.api_route("", methods=["GET", "HEAD"])
async def health_check():
    """Returns service health status."""
    return {"status": "healthy", "version": "v2.0.0"}

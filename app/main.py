"""
Kayan — FastAPI Backend
Intelligence & Orchestration Server (V2)
"""

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:    %(name)s - %(message)s",
)

app = FastAPI(
    title="Kayan API",
    description="AI-Native Elite Bio-Concierge — Intelligence & Orchestration Server",
    version="2.0.0",
)


# Custom middleware to ensure CORS headers are present on ALL responses,
# including 4xx/5xx error responses that FastAPI's CORSMiddleware may miss
# when exceptions are raised before the middleware can process the response.
ALLOWED_ORIGINS = {"http://localhost:3000", "http://127.0.0.1:3000"}


class CORSErrorMiddleware(BaseHTTPMiddleware):
    """Ensures CORS headers are always present, even on error responses."""

    async def dispatch(self, request: Request, call_next) -> Response:
        origin = request.headers.get("origin", "")

        # Handle preflight OPTIONS directly
        if request.method == "OPTIONS" and origin in ALLOWED_ORIGINS:
            return Response(
                status_code=200,
                headers={
                    "Access-Control-Allow-Origin": origin,
                    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, PATCH",
                    "Access-Control-Allow-Headers": "*",
                    "Access-Control-Allow-Credentials": "true",
                    "Access-Control-Max-Age": "3600",
                },
            )

        response = await call_next(request)

        # Inject CORS headers on every response if origin matches
        if origin in ALLOWED_ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Expose-Headers"] = "*"

        return response


# Add our custom CORS middleware FIRST (outermost = processes last on request, first on response)
app.add_middleware(CORSErrorMiddleware)

# Standard CORS middleware as a fallback for well-behaved responses
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# --- V2 Domain Routers ---
from app.routers.v2_health import router as health_router
from app.routers.v2_nutrition import router as nutrition_router
from app.routers.v2_training import router as training_router
from app.routers.v2_ai import router as ai_router
from app.routers.v2_telemetry import router as telemetry_router
from app.routers.v2_reports import router as reports_router
from app.routers.v2_profile import router as profile_v2_router
from app.routers.v2_analytics import router as analytics_router

app.include_router(health_router)
app.include_router(nutrition_router)
app.include_router(training_router)
app.include_router(ai_router)
app.include_router(telemetry_router)
app.include_router(reports_router)
app.include_router(profile_v2_router)
app.include_router(analytics_router)


@app.on_event("startup")
async def on_startup():
    """Application startup tasks."""
    import asyncio
    from app.services.scheduler import start_hourly_timezone_scheduler

    logger = logging.getLogger(__name__)
    logger.info("Kayan V2 API starting up...")

    # Launch the background timezone aggregation scheduler
    asyncio.create_task(start_hourly_timezone_scheduler())


@app.get("/")
async def root():
    return {"status": "ok", "service": "Kayan API", "version": "2.0.0"}

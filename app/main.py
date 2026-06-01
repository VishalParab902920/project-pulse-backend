"""
Kayan — FastAPI Backend
Intelligence & Orchestration Server (V2)
"""

import json
import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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


# ---------------------------------------------------------------------------
# CORS — Parse allowed origins from environment (JSON string list) or fallback
# ---------------------------------------------------------------------------
origins_env = os.getenv("ALLOWED_ORIGINS")
if origins_env:
    try:
        origins = json.loads(origins_env)
    except Exception:
        origins = ["http://localhost:3000"]
else:
    # Fallback: use settings or sensible defaults
    origins = settings.allowed_origins if settings.allowed_origins else ["http://localhost:3000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Global Exception Handler — ensures CORS headers are present on 500 errors
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logging.error(f"[GLOBAL EXCEPTION] Unhandled error: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "message": "Internal Server Error",
            "detail": str(exc),
        },
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

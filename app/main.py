"""
Project Pulse — FastAPI Backend
Intelligence & Orchestration Server
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import health, parse, entries, profile, memory, sync, analytics, byok
from app.services.seed import seed_dev_profile

# Configure logging so our debug messages actually show
logging.basicConfig(level=logging.INFO, format="%(levelname)s:    %(name)s - %(message)s")

app = FastAPI(
    title="Project Pulse API",
    description="AI-Native Fitness Concierge — Intelligence & Orchestration Server",
    version="0.1.0",
)

# CORS — allow the Next.js frontend to communicate
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Register Routers ---
app.include_router(health.router)
app.include_router(parse.router)
app.include_router(entries.router)
app.include_router(profile.router)
app.include_router(memory.router)
app.include_router(sync.router)
app.include_router(analytics.router)
app.include_router(byok.router)


@app.on_event("startup")
def on_startup():
    """Seed development data on server start."""
    if settings.environment == "development":
        seed_dev_profile()


@app.get("/")
async def root():
    return {"status": "ok", "service": "Project Pulse API", "version": "0.1.0"}

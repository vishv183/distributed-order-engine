"""
FastAPI Application Entry Point
=================================
Initializes the app, mounts routes, and handles startup/shutdown lifecycle.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.routes import router as webhook_router
from backend.app.models.db import init_db, seed_db

# ── Logging Configuration ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ── Lifespan (startup / shutdown) ────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create tables + seed.  Shutdown: cleanup."""
    logger.info("Application startup — initializing database.")
    await init_db()
    await seed_db()
    logger.info("Application startup complete.")
    yield
    logger.info("Application shutdown.")


# ── Application ──────────────────────────────────────────────────────────────
app = FastAPI(
    title="B2B Exception Engine",
    description=(
        "AI-powered order exception triage system using LangChain + Gemini. "
        "Ingests error telemetry via webhooks, processes them asynchronously "
        "with Celery workers, and resolves exceptions using tool-calling agents."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhook_router)


@app.get("/health", tags=["system"])
async def health_check() -> dict[str, str]:
    """Simple liveness probe."""
    return {"status": "healthy", "service": "b2b-exception-engine"}

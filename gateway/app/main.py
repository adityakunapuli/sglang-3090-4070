"""Gateway application entry point.

Initialises the FastAPI app, loads configuration at startup, creates
the upstream client and telemetry logger, and registers all route
handlers.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import load_config, GatewayConfig
from app.upstream import UpstreamClient
from app.telemetry import TelemetryLogger


# Suppress uvicorn access logs — only our custom logger talks to stdout
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan — runs once at startup and shutdown.

    Startup:
        1. Load configuration from ``config.yaml``.
        2. Create the upstream HTTP client (connection pool).
        3. Create the telemetry logger (rotating JSON file + SSE queue).

    Shutdown:
        1. Close the upstream HTTP client gracefully.
    """
    config_path = os.getenv("CONFIG_PATH", "config.yaml")
    config: GatewayConfig = load_config(config_path)

    upstream = UpstreamClient(
        api_base=config.api_base,
        timeout=120.0,
        max_connections=50,
    )

    telemetry = TelemetryLogger(config.logging)

    # Validate that every configured model is available in upstream /v1/models
    # (except for "auto" models which are resolved dynamically at request-time).
    upstream_models = await upstream.get_all_models()
    for alias, override in config.models.items():
        if override.model != "auto" and override.model not in upstream_models:
            raise ValueError(
                f"Configured model '{override.model}' for alias '{alias}' is not available "
                f"on the upstream backend. Available models: {upstream_models}"
            )

    # Attach to app.state for route handlers to access.
    _app.state.config = config
    _app.state.upstream = upstream
    _app.state.telemetry = telemetry

    yield

    await upstream.aclose()


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="LLM Gateway",
    description="Unified OpenAI-compatible proxy for LLM backends with "
    "per-model overrides, API key management, and structured telemetry.",
    version="2.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Register routers
# ---------------------------------------------------------------------------

from app.routes.health import router as health_router
from app.routes.models import router as models_router
from app.routes.chat import router as chat_router
from app.routes.dashboard import router as dashboard_router

app.include_router(health_router)
app.include_router(models_router)
app.include_router(chat_router)
app.include_router(dashboard_router)

# Serve static assets (favicon, etc.) under /static
_HERE = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(_HERE.parent / "static")), name="static")

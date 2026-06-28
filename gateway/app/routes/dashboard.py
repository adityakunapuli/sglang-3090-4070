"""Live dashboard routes — log streaming, JSON dump, and HTML page.

Serves:
- ``GET /`` — Terminal-style HTML page that streams log events via SSE.
- ``GET /logs/stream`` — Server-Sent Events endpoint emitting telemetry
  entries in real time.
- ``GET /logs/json`` — Returns the most recent N entries as a JSON array
  (useful for initial page load and Telegraf scraping).
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, StreamingResponse

from app.sse_helpers import stream_entries
from app.telemetry import TelemetryLogger

router = APIRouter(tags=["dashboard"])

_HERE = Path(__file__).resolve().parent.parent.parent
_STATIC_DIR = _HERE / "static"
_DASHBOARD_PATH = _STATIC_DIR / "dashboard.html"


@router.get("/")
async def dashboard_page():
    """Serve the terminal-style log viewer HTML page."""
    if _DASHBOARD_PATH.exists():
        return FileResponse(
            str(_DASHBOARD_PATH),
            media_type="text/html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
    return {"error": "dashboard.html not found", "path": str(_DASHBOARD_PATH)}


@router.get("/logs/stream")
async def log_stream(request: Request):
    """Server-Sent Events stream of telemetry entries.

    The first batch sends the 100 most recent entries as a replay for
    new clients, then streams new entries in real time. Heartbeat
    messages are sent every 15 s to keep the connection alive.
    """
    telemetry: TelemetryLogger = request.app.state.telemetry

    return StreamingResponse(
        stream_entries(
            telemetry.sse_queue,
            initial_batch=telemetry.recent_entries(100),
            heartbeat_interval=15.0,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/logs/json")
async def logs_json(request: Request, count: int = 100):
    """Return the most recent *count* telemetry entries as a JSON array.

    Query parameter ``?count=`` controls how many entries to return
    (default 100, max 500).
    """
    from dataclasses import asdict

    telemetry: TelemetryLogger = request.app.state.telemetry
    entries = telemetry.recent_entries(min(count, 500))
    return [asdict(e) for e in entries]

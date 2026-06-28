"""Server-Sent Events (SSE) formatting and streaming utilities.

Provides helpers to format Python dicts as SSE ``event:`` / ``data:``
lines and an async generator that yields entries from an asyncio queue
in real time.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from dataclasses import asdict
from typing import Any

from app.telemetry import TelemetryEntry


def format_sse(data: dict[str, Any], event_type: str | None = None) -> str:
    """Format a dictionary as one or more SSE ``data:`` lines.

    Args:
        data: Payload to serialize as JSON.
        event_type: Optional SSE ``event:`` field (e.g. ``"log"``,
            ``"heartbeat"``).

    Returns:
        A well-formed SSE string ending with ``\\n\\n``.
    """
    lines = []
    if event_type:
        lines.append(f"event: {event_type}")
    payload = json.dumps(data, default=str, ensure_ascii=False)
    for chunk in payload.split("\n"):
        lines.append(f"data: {chunk}")
    lines.append("")
    return "\n".join(lines) + "\n"


async def stream_entries(
    queue: asyncio.Queue[TelemetryEntry],
    *,
    initial_batch: list[TelemetryEntry] | None = None,
    heartbeat_interval: float = 15.0,
) -> AsyncGenerator[str, None]:
    """Continuously yield SSE-formatted telemetry entries from *queue*.

    Args:
        queue: The asyncio queue populated by ``TelemetryLogger``.
        initial_batch: Optional list of entries to emit immediately
            (e.g. the last N entries before the client connected).
        heartbeat_interval: Seconds between keepalive ``event:
            heartbeat`` messages.

    Yields:
        SSE-formatted strings suitable for ``StreamingResponse``.
    """
    if initial_batch:
        for entry in initial_batch:
            yield format_sse(asdict(entry), event_type="log")

    while True:
        try:
            entry = await asyncio.wait_for(queue.get(), timeout=heartbeat_interval)
            yield format_sse(asdict(entry), event_type="log")
        except asyncio.TimeoutError:
            yield format_sse({"ts": __import__("time").time()}, event_type="heartbeat")

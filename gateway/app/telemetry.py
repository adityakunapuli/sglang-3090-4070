"""Structured JSON logging with file rotation and real-time SSE streaming.

Each request results in a single JSON line written to a rotating log file
(ingestible by Telegraf) and pushed onto an in-memory ``asyncio.Queue`` for
the SSE dashboard to consume.
"""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import LoggingConfig


@dataclass
class TelemetryEntry:
    """One request's telemetry record.

    All fields use ``None`` defaults so partial records (e.g. error
    responses) are still well-formed.
    """

    timestamp: str = ""
    caller_host: str = ""
    caller_name: str = ""
    consumer: str = ""
    model: str = ""
    upstream_model: str = ""
    status_code: int = 0
    latency_ms: float = 0.0
    ttft_ms: float | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tokens_per_sec: float | None = None
    pp_speed: float | None = None
    error: str | None = None
    request_id: str = ""


class JsonFormatter(logging.Formatter):
    """Emit log records as newline-delimited JSON (for file/Telegraf)."""

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "telemetry"):
            data["telemetry"] = record.telemetry
        return json.dumps(data, default=str, ensure_ascii=False)


class HumanFormatter(logging.Formatter):
    """Human-readable one-line telemetry output for Docker logs."""

    def _ts_pacific(self, dt_str: str) -> str:
        try:
            from zoneinfo import ZoneInfo
            utc_dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            pt = utc_dt.astimezone(ZoneInfo("America/Los_Angeles"))
            return pt.strftime("%m/%d/%Y %I:%M:%S %p")
        except Exception:
            return dt_str[:19] if dt_str else "--"

    def format(self, record: logging.LogRecord) -> str:
        t = getattr(record, "telemetry", None)
        if not t or not isinstance(t, dict):
            return record.getMessage()

        ts = self._ts_pacific(t.get("timestamp", ""))
        consumer = t.get("consumer", "")
        model = t.get("model") or t.get("upstream_model", "?")
        pt = t.get("prompt_tokens", 0) or 0
        ct = t.get("completion_tokens", 0) or 0
        tok_s = t.get("tokens_per_sec")
        pp_s = t.get("pp_speed")
        ttft = t.get("ttft_ms")
        total = t.get("latency_ms", 0) or 0
        status = t.get("status_code", "?")
        error = t.get("error")

        caller_name = t.get("caller_name", "")
        caller_host = t.get("caller_host", "")
        if caller_name and caller_name != caller_host:
            caller_str = f"caller={caller_name}({caller_host})"
        else:
            caller_str = f"caller={caller_host or 'unknown'}"

        parts = [f"[{ts}]", caller_str]
        if consumer and consumer != "*anonymous*":
            parts.append(f"[{consumer}]")
        parts.append(f"[{model}]")
        parts.append(f"tokens={pt}\u2197{ct}")

        if pp_s is not None:
            parts.append(f"pp={pp_s:.1f}t/s")
        if tok_s is not None:
            parts.append(f"tok/s={tok_s:.2f}")
        if ttft is not None:
            parts.append(f"ttft={int(ttft)}ms")
        parts.append(f"total={int(total)}ms")

        if error:
            parts.append("FAILED")
            parts.append(f"status={status}")
            parts.append(f"ERROR={error}")

        return " ".join(parts)


_ANSI = {
    "gray": "\033[90m",
    "cyan": "\033[96m",
    "yellow": "\033[93m",
    "green": "\033[92m",
    "red": "\033[91m",
    "magenta": "\033[95m",
    "blue": "\033[94m",
    "bold": "\033[1m",
    "reset": "\033[0m",
}


class ColoredFormatter(logging.Formatter):
    """Human-readable telemetry with ANSI color codes (for terminal)."""

    def _ts_pacific(self, dt_str: str) -> str:
        try:
            from zoneinfo import ZoneInfo
            utc_dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
            pt = utc_dt.astimezone(ZoneInfo("America/Los_Angeles"))
            return pt.strftime("%m/%d/%Y %I:%M:%S %p")
        except Exception:
            return dt_str[:19] if dt_str else "--"

    def format(self, record: logging.LogRecord) -> str:
        t = getattr(record, "telemetry", None)
        if not t or not isinstance(t, dict):
            return record.getMessage()

        C = _ANSI
        ts = self._ts_pacific(t.get("timestamp", ""))
        consumer = t.get("consumer", "")
        model = t.get("model") or t.get("upstream_model", "?")
        pt = t.get("prompt_tokens", 0) or 0
        ct = t.get("completion_tokens", 0) or 0
        tok_s = t.get("tokens_per_sec")
        pp_s = t.get("pp_speed")
        ttft = t.get("ttft_ms")
        total = t.get("latency_ms", 0) or 0
        status = t.get("status_code", "?")
        error = t.get("error")

        parts = [f"{C['gray']}[{ts}]{C['reset']}"]

        caller_name = t.get("caller_name", "")
        caller_host = t.get("caller_host", "")
        if caller_name and caller_name != caller_host:
            caller_str = f"{C['gray']}caller={C['reset']}{C['bold']}{C['cyan']}{caller_name}{C['reset']}{C['gray']}({caller_host}){C['reset']}"
        else:
            caller_str = f"{C['gray']}caller={C['reset']}{C['bold']}{C['cyan']}{caller_host or 'unknown'}{C['reset']}"
        parts.append(caller_str)

        if consumer and consumer != "*anonymous*":
            parts.append(f"{C['bold']}{C['cyan']}[{consumer}]{C['reset']}")
        parts.append(f"{C['bold']}{C['yellow']}[{model}]{C['reset']}")
        parts.append(f"{C['bold']}{C['yellow']}tokens={pt}\u2197{ct}{C['reset']}")

        if pp_s is not None:
            parts.append(f"{C['bold']}{C['green']}pp={pp_s:.1f}t/s{C['reset']}")
        if tok_s is not None:
            parts.append(f"{C['bold']}{C['cyan']}tok/s={tok_s:.2f}{C['reset']}")
        if ttft is not None:
            parts.append(f"{C['bold']}{C['magenta']}ttft={int(ttft)}ms{C['reset']}")
        parts.append(f"{C['bold']}{C['red']}total={int(total)}ms{C['reset']}")

        if error:
            parts.append(f"{C['bold']}{C['red']}FAILED{C['reset']}")
            parts.append(f"{C['gray']}status={C['reset']}{C['bold']}{C['red']}{status}{C['reset']}")
            parts.append(f"{C['red']}{error}{C['reset']}")

        return " ".join(parts)


class TelemetryLogger:
    """Writes telemetry entries to a rotating JSON file and SSE queue.

    Usage (from route handlers)::

        telemetry = TelemetryLogger(config.logging)
        telemetry.log(TelemetryEntry(...))
    """

    def __init__(self, cfg: LoggingConfig) -> None:
        self._cfg = cfg
        self._queue: asyncio.Queue[TelemetryEntry] = asyncio.Queue(maxsize=2048)
        self._recent: list[TelemetryEntry] = []
        self._max_recent = 500

        # Ensure log directory exists
        log_path = Path(cfg.file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        self._logger = logging.getLogger("gateway.telemetry")
        self._logger.setLevel(cfg.level)
        self._logger.propagate = False

        # JSON file handler (for Telegraf/file rotation)
        file_handler = logging.handlers.RotatingFileHandler(
            filename=str(log_path),
            maxBytes=cfg.rotation_bytes,
            backupCount=cfg.max_files,
        )
        file_handler.setFormatter(JsonFormatter())
        self._logger.addHandler(file_handler)

        # Stderr handler (always unbuffered — for docker logs -f)
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(ColoredFormatter())
        self._logger.addHandler(stderr_handler)

    def log(self, entry: TelemetryEntry) -> None:
        """Record a telemetry entry to both persistent storage and the SSE queue.

        This is a synchronous call — the queue put is non-blocking
        (drops entries if the queue is full to avoid back-pressure on
        request processing).
        """
        record = logging.LogRecord(
            name="gateway.telemetry",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="",
            args=(),
            exc_info=None,
        )
        record.telemetry = asdict(entry)
        self._logger.handle(record)

        # Push to live SSE queue (non-blocking, drop if full)
        try:
            self._queue.put_nowait(entry)
        except asyncio.QueueFull:
            pass

        # Keep rolling window of recent entries
        self._recent.append(entry)
        if len(self._recent) > self._max_recent:
            self._recent.pop(0)

    @property
    def sse_queue(self) -> asyncio.Queue[TelemetryEntry]:
        """Queue consumed by the SSE log-stream endpoint."""
        return self._queue

    def recent_entries(self, count: int = 100) -> list[TelemetryEntry]:
        """Return the most recent *count* entries from the rolling window."""
        return self._recent[-count:]

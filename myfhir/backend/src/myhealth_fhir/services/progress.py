"""In-memory per-provider sync progress, surfaced to the dashboard over SSE.

A data-pull runs for minutes. While one is active the dashboard opens a
Server-Sent Events stream on ``GET /api/sync/{provider}/progress`` and renders
live stage / patient progress. This module holds the shared, thread-safe
progress state that the job and the FHIR client write to and the SSE endpoint
reads from.

State is best-effort and process-local: a progress entry exists only for the
duration of a run (plus a short tail so a late-connecting client can read the
final status). Nothing here is persisted.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class SyncProgress:
    """Progress snapshot for one provider's active (or just-finished) sync."""

    status: str = "idle"  # idle | running | done | failed | skipped | none
    stage: str = ""  # "starting" | "EOBs" | "Claims" | "Labs" | "Clinical"
    message: str = ""  # free-text detail (e.g. current clinical resource type)
    index: int = 0  # patients completed in the current stage
    total: int = 0  # total patients in the current stage
    current: str = ""  # current patient id
    started_at: float | None = field(default=None)
    finished_at: float | None = field(default=None)

    def snapshot(self) -> dict:
        elapsed = None
        if self.started_at is not None:
            end = self.finished_at if self.finished_at is not None else time.time()
            elapsed = round(max(0.0, end - self.started_at), 1)
        return {
            "status": self.status,
            "stage": self.stage,
            "message": self.message,
            "index": self.index,
            "total": self.total,
            "current": self.current,
            "elapsed_sec": elapsed,
        }


class ProgressRegistry:
    """Thread-safe map of provider -> SyncProgress."""

    def __init__(self) -> None:
        self._progress: dict[str, SyncProgress] = {}
        self._lock = threading.Lock()

    def reset(self, provider: str, stage: str = "starting", message: str = "") -> None:
        with self._lock:
            self._progress[provider] = SyncProgress(
                status="running", stage=stage, message=message, started_at=time.time()
            )

    def set_stage(self, provider: str, stage: str, total: int = 0, message: str = "") -> None:
        with self._lock:
            p = self._progress.get(provider)
            if p is None:
                p = SyncProgress(status="running", started_at=time.time())
                self._progress[provider] = p
            if p.status == "idle":
                return  # don't resurrect a finished/absent run from a stray update
            p.stage = stage
            p.total = total
            p.index = 0
            p.current = ""
            p.message = message

    def advance(self, provider: str, current: str, index: int, message: str | None = None) -> None:
        with self._lock:
            p = self._progress.get(provider)
            if p is None or p.status != "running":
                return
            p.current = current
            p.index = index
            if message is not None:
                p.message = message

    def finish(self, provider: str, status: str = "done", message: str = "") -> None:
        with self._lock:
            p = self._progress.get(provider)
            if p is None:
                p = SyncProgress()
                self._progress[provider] = p
            p.status = status
            p.message = message
            p.finished_at = time.time()

    def snapshot(self, provider: str) -> dict:
        with self._lock:
            p = self._progress.get(provider)
            return (p or SyncProgress()).snapshot()


# Module-level singleton shared by the job, the FHIR client, and the SSE endpoint.
registry = ProgressRegistry()

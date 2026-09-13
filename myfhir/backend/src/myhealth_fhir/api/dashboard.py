"""Focused dashboard API: job-run status, token/reauth state, and the OAuth round-trip.

Replaces the old (dropped) ``routes.py``. Serves only what the dashboard needs:

- ``GET /api/status``            — per-provider run summary, totals, token state
- ``GET /api/auth/{provider}/start``    — build the authorize URL (persists PKCE verifier)
- ``POST /api/auth/{provider}/exchange`` — exchange a pasted redirect URL for tokens
"""


import asyncio
import json
import logging
import threading
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from myhealth_fhir.config.settings import list_providers, resolve_provider
from myhealth_fhir.db import get_auth_session, get_session_for
from myhealth_fhir.db.models_auth import JobRun
from myhealth_fhir.services.auth import get_auth_manager
from myhealth_fhir.services.fhir_client import get_fhir_client
from myhealth_fhir.services.progress import registry

log = logging.getLogger("myhealth_fhir.api")

router = APIRouter(prefix="/api", tags=["dashboard"])
_PROVIDERS = list_providers()


class ExchangeRequest(BaseModel):
    """Request body for the manual-paste token exchange."""

    redirect_url: str
    code: str | None = None


def _parse_code(raw: str) -> str:
    """Extract the OAuth authorization code from a redirect URL or raw code string."""
    from urllib.parse import parse_qs, urlparse

    raw = (raw or "").strip()
    parse_url = raw if "://" in raw else f"http://dummy?{raw.lstrip('?')}"
    qs = parse_qs(urlparse(parse_url).query)
    if "code" in qs and qs["code"]:
        return qs["code"][0].strip()
    return raw


def _iso_utc(dt: datetime | None) -> str | None:
    """Serialize a possibly-naive (stored-as-UTC) datetime to an explicit UTC ISO string."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _last_run(provider: str) -> dict | None:
    """Return the most recent JobRun row for a provider as a plain dict."""
    with get_auth_session() as session:
        row = (
            session.query(JobRun)
            .filter(JobRun.provider == provider)
            .order_by(JobRun.started_at.desc())
            .first()
        )
        if row is None:
            return None
        return {
            "started_at": _iso_utc(row.started_at),
            "finished_at": _iso_utc(row.finished_at),
            "status": row.status,
            "counts": row.counts,
            "error": row.error,
        }


def _token_health(provider: str) -> dict:
    """Return per-patient token state used to drive the REAUTH banner."""
    manager = get_auth_manager(provider)
    info = manager.status()
    patients: list[dict] = []
    reauth_needed = False
    for p in info.get("patients", []):
        state = "valid"
        reason: str | None = None
        if p.get("refresh_expired"):
            state, reason = "reauth", "refresh token expired"
        elif p.get("expired") and not p.get("has_refresh_token"):
            state, reason = "reauth", "access expired, no refresh token"
        elif p.get("expired"):
            state, reason = "expired", "access expired (auto-refresh pending)"
        elif p.get("seconds_remaining", 0) < 300:
            state, reason = "expiring", "near expiry"
        if state in ("reauth", "expired"):
            reauth_needed = True
        patients.append(
            {
                "patient_id": p.get("patient_id"),
                "patient_name": p.get("patient_name"),
                "state": state,
                "reason": reason,
                "seconds_remaining": p.get("seconds_remaining"),
                "expires_at": p.get("expires_at"),
            }
        )
    default_only = bool(patients) and all(p["patient_id"] == "_default" for p in patients)
    if default_only:
        reauth_needed = True
        for patient in patients:
            patient["state"] = "setup"
            patient["reason"] = "default token; authenticate a patient session"
    if not patients:
        reauth_needed = True
    return {
        "authenticated": bool(info.get("authenticated")),
        "patients": patients,
        "reauth_needed": reauth_needed,
        "default_only": default_only,
    }


def _totals(provider: str) -> dict[str, int]:
    """Read cached row counts for a provider's downstream tables."""
    from myhealth_fhir.job import _COUNTERS

    tables = _COUNTERS.get(provider, [])
    from sqlalchemy import text

    totals: dict[str, int] = {}
    try:
        with get_session_for(provider) as session:
            for table in tables:
                totals[table] = session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
    except Exception:
        pass
    return totals


@router.get("/status")
def status() -> dict:
    """Return per-provider status: last run, totals, and token/reauth health."""
    providers: dict[str, dict] = {}
    for provider in _PROVIDERS:
        run = _last_run(provider)
        tokens = _token_health(provider)
        totals = _totals(provider)
        providers[provider] = {
            "display_name": resolve_provider(provider).display_name,
            "kind": resolve_provider(provider).kind,
            "last_run": run,
            "totals": totals,
            "auth": tokens,
        }
    return {"generated_at": datetime.now(UTC).isoformat(), "providers": providers}


@router.get("/auth/{provider}/start")
def auth_start(provider: str) -> dict:
    """Build an authorize URL for a provider and persist its PKCE verifier."""
    if provider not in _PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider '{provider}'")
    import secrets

    manager = get_auth_manager(provider)
    state = secrets.token_urlsafe(16)
    return {"authorize_url": manager.build_authorize_url(state=state), "state": state}


@router.post("/auth/{provider}/exchange")
def auth_exchange(provider: str, body: ExchangeRequest) -> dict:
    """Exchange a pasted redirect URL (or raw code) into stored tokens."""
    if provider not in _PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider '{provider}'")
    manager = get_auth_manager(provider)
    code = body.code or _parse_code(body.redirect_url)
    if not code:
        raise HTTPException(status_code=422, detail="No authorization code found in redirect URL")
    try:
        token = manager.exchange_code(code)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {e}")

    pid = token.patient_id or "default"
    patient_name = token.patient_name
    # Attempt to resolve a nicer display name from the provider (best-effort).
    try:
        name = get_fhir_client(provider).resolve_patient_name(pid)
        if name:
            patient_name = name
    except Exception:
        pass
    return {"message": "Token stored", "patient_id": pid, "patient_name": patient_name, "scope": token.scope}


@router.get("/providers")
def list_available_providers() -> dict:
    """Return the configured providers and their kind (anthem vs epic)."""
    return {
        "providers": [
            {"name": name, "display_name": resolve_provider(name).display_name, "kind": resolve_provider(name).kind}
            for name in _PROVIDERS
        ]
    }


@router.post("/sync/{provider}")
def sync_now(provider: str) -> dict:
    """Trigger an on-demand iterative data sync for a single provider.

    Starts the provider's pull in a background thread and returns immediately.
    A 409 is returned if a sync for that provider is already in progress.
    """
    if provider not in _PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider '{provider}'")
    from myhealth_fhir.job import is_provider_running, run_provider

    if is_provider_running(provider):
        raise HTTPException(status_code=409, detail="A sync is already in progress for this provider")

    def _worker() -> None:
        try:
            run_provider(provider)
        except Exception:
            log.exception("Manual sync for provider %s failed", provider)

    # Reset synchronously so a progress stream opened right after this returns
    # sees a live "running" state (no idle gap before the worker thread starts).
    registry.reset(provider, stage="starting", message="Starting sync")
    threading.Thread(target=_worker, name=f"manual-sync-{provider}", daemon=True).start()
    return {"started": True, "provider": provider, "message": f"Sync started for {provider}"}


@router.get("/sync/{provider}/progress")
async def sync_progress(provider: str):
    """Stream live sync progress for a provider as Server-Sent Events.

    Emits a JSON snapshot on every change, a heartbeat comment every ~15s while
    the connection is otherwise quiet, and closes once the run reaches a terminal
    state (done/failed/skipped) — or if no run has started within ~15s of connect.
    """
    if provider not in _PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider '{provider}'")

    async def event_stream():
        last: dict | None = None
        last_send = asyncio.get_event_loop().time()
        idle_seconds = 0.0
        yield ": connected\n\n"
        while True:
            snap = registry.snapshot(provider)
            now = asyncio.get_event_loop().time()
            if snap["status"] == "idle":
                idle_seconds += 0.5
                if idle_seconds > 15:
                    yield f"data: {json.dumps({**snap, 'status': 'none'})}\n\n"
                    return
                await asyncio.sleep(0.5)
                continue
            idle_seconds = 0.0
            if snap != last:
                last = snap
                yield f"data: {json.dumps(snap)}\n\n"
                last_send = now
            elif now - last_send >= 15:
                yield ": heartbeat\n\n"
                last_send = now
            if snap["status"] in ("done", "failed", "skipped"):
                await asyncio.sleep(1.0)
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )

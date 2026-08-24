"""Focused dashboard API: job-run status, token/reauth state, and the OAuth round-trip.

Replaces the old (dropped) ``routes.py``. Serves only what the dashboard needs:

- ``GET /api/status``            — per-provider run summary, totals, token state
- ``GET /api/auth/{provider}/start``    — build the authorize URL (persists PKCE verifier)
- ``POST /api/auth/{provider}/exchange`` — exchange a pasted redirect URL for tokens
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from myhealth_fhir.config.settings import list_providers, resolve_provider
from myhealth_fhir.db import get_auth_session, get_session_for
from myhealth_fhir.db.models_auth import JobRun
from myhealth_fhir.services.auth import get_auth_manager
from myhealth_fhir.services.fhir_client import get_fhir_client

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
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
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

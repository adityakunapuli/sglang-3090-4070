"""API key authentication via Bearer tokens.

Config-driven, **permissive by default** — if no ``keys.allowed_tokens``
are configured, all requests pass through unchallenged. This allows a
gradual rollout: configure consumer environment variables first, then
enable auth in the gateway config when ready.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request, HTTPException, status

from app.config import GatewayConfig


# Sentinel value used to mark "no auth" internally.
_AUTH_DISABLED = object()


def _extract_bearer(request: Request) -> str | None:
    """Pull the Bearer token from the ``Authorization`` header, if present."""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def authenticate_request(request: Request, config: GatewayConfig) -> str:
    """Validate the request's Bearer token against the gateway config.

    Returns a consumer name (or ``"*anonymous*"``) on success.

    Raises:
        HTTPException(401): When the token is invalid or missing and
            auth is enforced.
    """
    token = _extract_bearer(request)

    resolved_consumer = "*anonymous*"
    if token:
        for consumer, key in config.per_consumer_keys.items():
            if key == token:
                resolved_consumer = consumer
                break
        if resolved_consumer == "*anonymous*" and token in config.allowed_tokens:
            resolved_consumer = "*authenticated*"

    if not config.has_auth:
        return resolved_consumer

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if resolved_consumer == "*anonymous*":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return resolved_consumer

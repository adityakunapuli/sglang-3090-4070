"""Liveness and readiness probe endpoints."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    """Simple liveness check.

    Returns a 200 OK when the service is running.
    """
    return {"status": "healthy"}


@router.get("/ready")
async def readiness():
    """Readiness check — confirms the gateway can handle requests.

    Currently just a synonym for ``/health``. In the future could
    check upstream connectivity.
    """
    return {"status": "ready"}

"""Tests for the chat completions proxy endpoint."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_missing_body_returns_400(client: AsyncClient) -> None:
    """Sending invalid JSON returns 400, not 401."""
    resp = await client.post(
        "/v1/chat/completions",
        content=b"not-json",
        headers={"Authorization": "Bearer sk-password"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_no_auth_passes_through(client: AsyncClient) -> None:
    """When auth is not configured requests pass (but fail on bad JSON)."""
    resp = await client.post(
        "/v1/chat/completions",
        content=b"not-json",
    )
    assert resp.status_code == 400

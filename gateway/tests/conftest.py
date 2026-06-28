"""Shared fixtures for gateway tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.config import GatewayConfig, ModelOverride, LoggingConfig


@pytest.fixture
def sample_config() -> GatewayConfig:
    """GatewayConfig with auth enabled and two model aliases."""
    return GatewayConfig(
        api_base="http://test-backend:8080/v1/chat/completions",
        allowed_tokens=("sk-secret",),
        per_consumer_keys={"frigate": "sk-frigate"},
        models={
            "frigate": ModelOverride(
                model="auto",
                temperature=1.1,
                max_tokens=512,
                max_model_len=8192,
                context_length=8192,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            ),
            "ocr": ModelOverride(
                model="auto",
                temperature=0.0,
                max_tokens=8192,
                max_model_len=65536,
                context_length=32768,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            ),
        },
        logging=LoggingConfig(file="/tmp/gateway-test.json", level="DEBUG"),
    )


@pytest.fixture
def no_auth_config() -> GatewayConfig:
    """GatewayConfig with no keys section — all requests pass through."""
    return GatewayConfig(
        api_base="http://test-backend:8080/v1/chat/completions",
        allowed_tokens=(),
        models={},
        logging=LoggingConfig(file="/tmp/gateway-test-noauth.json", level="DEBUG"),
    )


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """FastAPI test client with lifespan management."""
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

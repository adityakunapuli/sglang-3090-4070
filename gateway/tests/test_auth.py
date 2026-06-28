"""Tests for API key authentication middleware."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.auth import authenticate_request
from app.config import GatewayConfig


def _make_request(headers: dict | None = None) -> type:
    """Build a minimal mock Request-like object for auth testing."""
    class MockRequest:
        def __init__(self, hdrs):
            self.headers = hdrs or {}

    return MockRequest


class TestAuthenticateRequest:
    def test_no_auth_config_passes_all(self, no_auth_config: GatewayConfig) -> None:
        """When no allowed_tokens are configured, any request passes."""
        req_cls = _make_request()
        req = req_cls({"Authorization": "Bearer whatever"})
        result = authenticate_request(req, no_auth_config)
        assert result == "*anonymous*"

    def test_no_auth_config_no_header(self, no_auth_config: GatewayConfig) -> None:
        """Without auth config, even missing header passes."""
        req_cls = _make_request()
        req = req_cls({})
        result = authenticate_request(req, no_auth_config)
        assert result == "*anonymous*"

    def test_valid_global_token(self, sample_config: GatewayConfig) -> None:
        """A token in allowed_tokens returns '*authenticated*'."""
        req_cls = _make_request()
        req = req_cls({"Authorization": "Bearer sk-secret"})
        result = authenticate_request(req, sample_config)
        assert result == "*authenticated*"

    def test_valid_per_consumer_token(self, sample_config: GatewayConfig) -> None:
        """A consumer-specific token returns that consumer's name."""
        req_cls = _make_request()
        req = req_cls({"Authorization": "Bearer sk-frigate"})
        result = authenticate_request(req, sample_config)
        assert result == "frigate"

    def test_missing_token_raises_401(self, sample_config: GatewayConfig) -> None:
        """Missing Authorization header raises 401."""
        req_cls = _make_request()
        req = req_cls({})
        with pytest.raises(HTTPException) as exc:
            authenticate_request(req, sample_config)
        assert exc.value.status_code == 401

    def test_invalid_token_raises_401(self, sample_config: GatewayConfig) -> None:
        """An unknown token raises 401."""
        req_cls = _make_request()
        req = req_cls({"Authorization": "Bearer invalid-key"})
        with pytest.raises(HTTPException) as exc:
            authenticate_request(req, sample_config)
        assert exc.value.status_code == 401

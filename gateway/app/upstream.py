"""Upstream backend HTTP client with model resolution and payload hoisting.

Manages a connection pool to the LLM backend (llama-swap or llama-cpp)
and provides helpers for:
- Resolving the currently active model
- Querying upstream context length
- Hoisting ``extra_body`` keys for llama-server compatibility
"""

from __future__ import annotations

import re
import socket
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

import asyncio

import httpx


@dataclass
class ModelInfo:
    """Information about a model loaded on the upstream backend."""

    name: str
    state: str = ""
    cmd: str = ""


# ---------------------------------------------------------------------------
# Simple TTL-based DNS cache — avoids repeated reverse lookups of the same
# client IP within a short window.
# ---------------------------------------------------------------------------
_dns_cache: dict[str, tuple[str, float]] = {}
_DNS_TTL = 300.0

_active_model_cache: tuple[str, float] | None = None
_ACTIVE_MODEL_TTL = 15.0


def resolve_host(host: str) -> str:
    """Reverse-resolve *host* to a canonical name, cached for ``_DNS_TTL`` s."""
    now = time.time()
    cached = _dns_cache.get(host)
    if cached and (now - cached[1]) < _DNS_TTL:
        return cached[0]
    try:
        name, _, _ = socket.gethostbyaddr(host)
        _dns_cache[host] = (name, now)
        return name
    except (socket.herror, socket.gaierror):
        return host


# ---------------------------------------------------------------------------
# Utilities for extracting model info from the upstream API.
# ---------------------------------------------------------------------------

def _extract_ctx_from_cmd(cmd: str) -> int | None:
    """Parse ``--ctx-size`` (or short ``-c``) from a llama-server command line."""
    match = re.search(r"(?:--ctx-size|-c)\s+(\d+)", cmd)
    return int(match.group(1)) if match else None


def _ensure_host(url: str, host: str) -> str:
    """Replace the hostname portion of *url* with *host*.

    Used when a consumer's URL needs to target a specific Docker container
    name instead of a raw IP.
    """
    try:
        parsed = httpx.URL(url)
        return str(parsed.copy_with(host=host))
    except Exception:
        return url


# ---------------------------------------------------------------------------
# Upstream client — connection pool + backend query helpers.
# ---------------------------------------------------------------------------

class UpstreamClient:
    """Pooled HTTP client that talks to the LLM inference backend.

    Usage::

        upstream = UpstreamClient("http://192.168.254.111:8082/v1/chat/completions")
        model = await upstream.get_active_model()
    """

    def __init__(
        self, api_base: str, timeout: float = 120.0, max_connections: int = 50
    ) -> None:
        # Derive the base URL for model-list / running endpoints from the
        # chat completions URL (strip ``/v1/chat/completions``).
        self._api_base = api_base
        self._backend_root = api_base.removesuffix("/v1/chat/completions").removesuffix(
            "/chat/completions"
        )

        limits = httpx.Limits(
            max_keepalive_connections=max_connections,
            max_connections=max_connections,
            keepalive_expiry=30.0,
        )
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            limits=limits,
            follow_redirects=True,
        )

    @property
    def api_base(self) -> str:
        return self._api_base

    @property
    def client(self) -> httpx.AsyncClient:
        """Expose the underlying ``httpx.AsyncClient`` for direct use in routes."""
        return self._client

    async def get_all_models(self) -> list[str]:
        """Fetch all model IDs returned by the upstream backend /v1/models.

        Tries to fetch the model list multiple times with retries to allow
        for upstream startup time.
        """
        import logging
        for attempt in range(5):
            try:
                resp = await self._client.get(f"{self._backend_root}/v1/models")
                if resp.status_code == 200:
                    data = resp.json().get("data", [])
                    return [m.get("id") for m in data if m.get("id")]
                else:
                    logging.warning(
                        f"Upstream /v1/models returned status {resp.status_code} "
                        f"on attempt {attempt + 1}"
                    )
            except httpx.HTTPError as e:
                logging.warning(
                    f"Upstream connection attempt {attempt + 1} failed: {e}"
                )
            await asyncio.sleep(2.0)
        raise RuntimeError(
            f"Failed to fetch models from upstream backend at {self._backend_root} "
            f"after 5 attempts."
        )

    async def get_active_model(self) -> str | None:
        """Query upstream for the currently active (ready) model.

        Results are cached for ``_ACTIVE_MODEL_TTL`` seconds to avoid
        hammering the upstream on every request.  On any upstream error
        (HTTP error, no ready model found, etc.) the cache is
        invalidated immediately so the next call re-fetches — this
        handles llama-swap model transitions transparently.

        Tries **llama-swap** style ``/running`` first, then falls back to
        standard ``/v1/models`` (llama-cpp / OpenAI-compatible).

        Returns:
            The model identifier string, or ``None`` if no ready model is
            found.
        """
        global _active_model_cache

        now = time.time()
        if _active_model_cache is not None and (now - _active_model_cache[1]) < _ACTIVE_MODEL_TTL:
            return _active_model_cache[0]

        model = None

        # 1. llama-swap /running endpoint
        try:
            resp = await self._client.get(f"{self._backend_root}/running")
            if resp.status_code == 200:
                for m in resp.json().get("running", []):
                    if m.get("state") == "ready":
                        model = m.get("model")
                        break
        except httpx.HTTPError:
            pass

        # 2. Fallback to OpenAI-compatible /v1/models
        if model is None:
            try:
                resp = await self._client.get(f"{self._backend_root}/v1/models")
                if resp.status_code == 200:
                    models = resp.json().get("data", [])
                    if models:
                        model = models[0].get("id")
            except httpx.HTTPError:
                pass

        if model is not None:
            _active_model_cache = (model, now)
            return model

        # 3. Failure — invalidate cache and return fallback
        _active_model_cache = None
        return "Gemma4-26B-A4B-QAT"

    async def get_context_length(self, active_model: str | None = None) -> int | None:
        """Query upstream for the backend's configured context length.

        Args:
            active_model: If given, narrows the query to that model's
                server process in llama-swap.

        Returns:
            Context size in tokens, or ``None`` if it cannot be determined.
        """
        # 1. Try llama-swap /running with model-specific cmd parsing
        try:
            resp = await self._client.get(f"{self._backend_root}/running")
            if resp.status_code == 200:
                for m in resp.json().get("running", []):
                    if m.get("state") == "ready":
                        if active_model and m.get("model") != active_model:
                            continue
                        ctx = _extract_ctx_from_cmd(m.get("cmd", ""))
                        if ctx:
                            return ctx
        except httpx.HTTPError:
            pass

        # 2. Try llama-cpp /props
        try:
            resp = await self._client.get(f"{self._backend_root}/props")
            if resp.status_code == 200:
                return (
                    resp.json()
                    .get("default_generation_settings", {})
                    .get("n_ctx")
                )
        except httpx.HTTPError:
            pass

        return None

    @staticmethod
    def hoist_extra_body(body: dict[str, Any]) -> dict[str, Any]:
        """Move ``extra_body`` keys to the root of *body*.

        ``llama-server`` expects OpenAI ``extra_body`` keys such as
        ``chat_template_kwargs`` at the request root rather than nested.
        This mutates the body **in place** and returns it for convenience.
        """
        extra = body.pop("extra_body", None)
        if isinstance(extra, dict):
            for key, value in extra.items():
                if key in body and isinstance(body[key], dict) and isinstance(value, dict):
                    body[key].update(value)
                else:
                    body[key] = value
        return body

    @staticmethod
    def add_stream_options(body: dict[str, Any]) -> dict[str, Any]:
        """Inject ``stream_options`` when streaming is enabled.

        Ensures upstream includes usage metadata in the final chunk.
        """
        if body.get("stream", False) and "stream_options" not in body:
            body["stream_options"] = {"include_usage": True}
        return body

    async def get_loading_state(self) -> dict | None:
        """Check if llama-swap is currently loading a model.

        Returns the loading model info dict (with state=="loading") if
        a model is transitioning, or ``None`` if idle.
        """
        try:
            resp = await self._client.get(f"{self._backend_root}/running")
            if resp.status_code == 200:
                for m in resp.json().get("running", []):
                    if m.get("state") == "loading":
                        return m
        except httpx.HTTPError:
            pass
        return None

    async def wait_for_model_ready(
        self, model_name: str, poll_interval: float = 2.0, timeout: float = 180.0
    ) -> bool:
        """Poll ``/running`` until *model_name* is in ``"ready"`` state.

        Returns ``True`` once ready, ``False`` if the timeout expires.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = await self._client.get(f"{self._backend_root}/running")
                if resp.status_code == 200:
                    for m in resp.json().get("running", []):
                        if m.get("model") == model_name and m.get("state") == "ready":
                            return True
            except httpx.HTTPError:
                pass
            await asyncio.sleep(poll_interval)
        return False

    async def aclose(self) -> None:
        """Gracefully close the underlying HTTP client."""
        await self._client.aclose()

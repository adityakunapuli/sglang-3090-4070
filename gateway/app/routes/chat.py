"""Core proxy endpoint — ``POST /v1/chat/completions``.

Intercepts chat-completion requests, applies per-model overrides
(temperature, max_tokens, reasoning on/off), resolves ``"auto"``
model aliases, hoists ``extra_body`` keys for llama-server
compatibility, waits for model readiness during swaps, rate-limits
requests to prevent backend thrashing, and streams the response
back with telemetry capture.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator


from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from app.auth import authenticate_request
from app.telemetry import TelemetryEntry, TelemetryLogger
from app.upstream import UpstreamClient

router = APIRouter(tags=["chat"])

# ── Simple sliding-window rate limiter ───────────────────────────────────
_request_timestamps: list[float] = []

# ── Priority admission control state ────────────────────────────────────
_active_requests: dict[str, int] = {}
_active_lock = asyncio.Lock()

# ── Consumer cooldown tracking (e.g. prevent frigate from starting
#     within N seconds after any other request completes) ────────────────
_last_activity_finish: float = 0.0


async def _admit_request(consumer: str, config) -> bool:
    """Check if *consumer* may proceed.

    Returns ``True`` to admit, ``False`` to reject with 503.
    A consumer is rejected when:
    1. A **higher-priority** consumer (lower number) has an active
       in-flight request.
    2. The consumer has a **cooldown** configured and a request from
       any consumer finished within the cooldown window.
    """
    async with _active_lock:
        # Cooldown check: reject if this consumer is in a quiet period
        # following any recent request completion.
        cooldown = config.consumer_cooldown.get(consumer, 0)
        if cooldown > 0:
            elapsed = time.time() - _last_activity_finish
            if elapsed < cooldown:
                return False

        incoming = config.consumer_priorities.get(consumer, 0)
        for active_consumer, count in _active_requests.items():
            if count > 0:
                active = config.consumer_priorities.get(active_consumer, 0)
                if incoming > active:
                    return False
        _active_requests[consumer] = _active_requests.get(consumer, 0) + 1
        return True


async def _release_request(consumer: str) -> None:
    """Decrement the active-request counter for *consumer*."""
    global _last_activity_finish
    async with _active_lock:
        current = _active_requests.get(consumer, 0)
        if current > 1:
            _active_requests[consumer] = current - 1
        else:
            _active_requests.pop(consumer, None)
    # Any request completion resets the cooldown timer so low-priority
    # consumers (e.g. frigate) wait a full cooldown window before starting.
    _last_activity_finish = time.time()
# ─────────────────────────────────────────────────────────────────────────


def _check_rate_limit(rpm: int | None) -> None:
    """Raise ``HTTPException(429)`` if the RPM limit is exceeded."""
    if rpm is None or rpm <= 0:
        return
    now = time.time()
    window = 60.0
    cutoff = now - window
    # Prune expired entries
    while _request_timestamps and _request_timestamps[0] < cutoff:
        _request_timestamps.pop(0)
    if len(_request_timestamps) >= rpm:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again shortly.")
    _request_timestamps.append(now)
# ─────────────────────────────────────────────────────────────────────────



# ---------------------------------------------------------------------------
# Override application logic
# ---------------------------------------------------------------------------

def _apply_overrides(body: dict, config, model_name: str) -> str | None:
    """Apply per-model overrides from *config* to *body* in place.

    If the model alias specifies ``model: "auto"`` this function returns
    ``"auto"`` to signal that the caller should resolve the active model
    dynamically.

    Returns:
        The resolved model name string (e.g. ``"Gemma-4-12B-MTP"``), or
        ``None`` if no override matched, or ``"auto"`` if resolution is
        needed.
    """
    override = config.models.get(model_name)
    if not override:
        return None

    overrides = {
        k: v for k, v in {
            "model": override.model,
            "temperature": override.temperature,
            "top_p": override.top_p,
            "frequency_penalty": override.frequency_penalty,
            "presence_penalty": override.presence_penalty,
            "max_tokens": override.max_tokens,
        }.items() if v is not None
    }

    for key, value in overrides.items():
        if isinstance(value, dict) and key in body and isinstance(body[key], dict):
            body[key].update(value)
        else:
            body[key] = value

    if override.extra_body:
        for ek, ev in override.extra_body.items():
            if ek in body and isinstance(body[ek], dict) and isinstance(ev, dict):
                body[ek].update(ev)
            else:
                body[ek] = ev

    if overrides.get("model") == "auto":
        return "auto"

    return overrides.get("model")


# ---------------------------------------------------------------------------
# Reasoning effort → llama.cpp params translation
# ---------------------------------------------------------------------------
_REASONING_EFFORT_MAP: dict[str, dict] = {
    "none": {
        "thinking_budget_tokens": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    },
    "off": {
        "thinking_budget_tokens": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    },
    "minimal": {
        "thinking_budget_tokens": 512,
        "chat_template_kwargs": {"enable_thinking": True, "preserve_thinking": True},
    },
    "low": {
        "thinking_budget_tokens": 1024,
        "chat_template_kwargs": {"enable_thinking": True, "preserve_thinking": True},
    },
    "medium": {
        "thinking_budget_tokens": 4096,
        "chat_template_kwargs": {"enable_thinking": True, "preserve_thinking": True},
    },
    "high": {
        "thinking_budget_tokens": -1,
        "chat_template_kwargs": {"enable_thinking": True, "preserve_thinking": True},
    },
    "xhigh": {
        "thinking_budget_tokens": -1,
        "chat_template_kwargs": {"enable_thinking": True, "preserve_thinking": True},
    },
    "max": {
        "thinking_budget_tokens": -1,
        "chat_template_kwargs": {"enable_thinking": True, "preserve_thinking": True},
    },
}


_LOG = logging.getLogger("gateway.chat")


def _apply_reasoning_effort(body: dict) -> None:
    """Translate ``reasoning_effort`` (from opencode variants) to llama.cpp params.

    Mutates *body* in place — replaces ``reasoning_effort`` with
    ``thinking_budget_tokens`` and merges ``chat_template_kwargs``.
    """
    effort = body.pop("reasoning_effort", None)
    if not effort:
        return

    params = _REASONING_EFFORT_MAP.get(effort)
    if not params:
        _LOG.warning("Unrecognized reasoning_effort value: %s", effort)
        return

    body["thinking_budget_tokens"] = params["thinking_budget_tokens"]

    ctk = body.get("chat_template_kwargs")
    if isinstance(ctk, dict):
        ctk.update(params["chat_template_kwargs"])
    else:
        body["chat_template_kwargs"] = dict(params["chat_template_kwargs"])

    _LOG.info(
        "reasoning_effort=%s → thinking_budget_tokens=%s chat_template_kwargs=%s",
        effort, params["thinking_budget_tokens"], params["chat_template_kwargs"],
    )


async def _build_upstream_body(
    body: dict, upstream: UpstreamClient, config, model_name: str
) -> tuple[str, str]:
    """Prepare the upstream request body and resolve model names.

    Returns:
        ``(resolved_model, upstream_model)`` — the first is logged in
        telemetry, the second is sent to the backend.

    Raises:
        HTTPException(503): If ``"auto"`` resolution finds no ready model.
    """
    action = _apply_overrides(body, config, model_name)
    _apply_reasoning_effort(body)

    if action == "auto":
        active = await upstream.get_active_model()
        if not active:
            raise HTTPException(status_code=503, detail="No active model available on backend")
        body["model"] = active
        upstream_model = active
        resolved = model_name
    elif action is not None:
        upstream_model = action
        resolved = model_name
    else:
        upstream_model = model_name
        resolved = model_name

    upstream.hoist_extra_body(body)
    upstream.add_stream_options(body)

    return resolved, upstream_model


# ---------------------------------------------------------------------------
# Streaming response wrapper with telemetry capture
# ---------------------------------------------------------------------------

async def _stream_and_log(
    resp,
    start_time: float,
    entry: TelemetryEntry,
    telemetry: TelemetryLogger,
) -> AsyncGenerator[bytes, None]:
    """Wrap an already-open streaming response, capturing TTFT and usage.

    Yields bytes as they arrive from *resp*. After the stream completes,
    finalises and persists the telemetry entry.
    """
    time_to_first_token: float | None = None
    usage_data: dict | None = None

    async for chunk in resp.aiter_bytes():
        if time_to_first_token is None:
            time_to_first_token = time.time()
        if b'"usage"' in chunk:
            try:
                decoded = chunk.decode("utf-8")
                for line in decoded.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("data: "):
                        line = line[6:].strip()
                    if line and line != "[DONE]":
                        try:
                            parsed = json.loads(line)
                            if parsed.get("usage"):
                                usage_data = parsed["usage"]
                        except json.JSONDecodeError:
                            pass
            except UnicodeDecodeError:
                pass
        yield chunk

    # Finalise telemetry after stream completes.
    elapsed = time.time() - start_time
    ttft = (time_to_first_token - start_time) if time_to_first_token else 0.0
    pt = (usage_data or {}).get("prompt_tokens", 0)
    ct = (usage_data or {}).get("completion_tokens", 0)

    entry.latency_ms = round(elapsed * 1000, 1)
    entry.ttft_ms = round(ttft * 1000, 1) if ttft > 0 else None
    entry.prompt_tokens = pt
    entry.completion_tokens = ct
    entry.tokens_per_sec = round(ct / elapsed, 2) if elapsed > 0 else None
    entry.pp_speed = round(pt / ttft, 1) if pt > 0 and ttft > 0 else None

    telemetry.log(entry)


async def _stream_with_release(
    resp,
    start_time: float,
    entry: TelemetryEntry,
    telemetry: TelemetryLogger,
    consumer: str,
) -> AsyncGenerator[bytes, None]:
    """Wrap ``_stream_and_log``, releasing the consumer slot on completion."""
    try:
        async for chunk in _stream_and_log(resp, start_time, entry, telemetry):
            yield chunk
    finally:
        await _release_request(consumer)


# ---------------------------------------------------------------------------
# Route handler
# ---------------------------------------------------------------------------

@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """Proxy a chat-completion request to the upstream LLM backend.

    Applies per-model overrides from ``config.yaml``, resolves ``"auto"``
    aliases, hoists ``extra_body`` keys, and streams the response back
    with telemetry capture.

    The authenticated consumer name is resolved from the Bearer token
    in the ``Authorization`` header (permissive-by-default).
    """
    start_time = time.time()
    config = request.app.state.config
    upstream: UpstreamClient = request.app.state.upstream
    telemetry: TelemetryLogger = request.app.state.telemetry

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    model_name = body.get("model", "unknown")
    consumer = authenticate_request(request, config)

    # ── Priority admission control ───────────────────────────────────────
    if config.has_qos and not await _admit_request(consumer, config):
        raise HTTPException(
            status_code=503,
            detail=f"Model busy with higher-priority request. Consumer '{consumer}' queued.",
        )
    # ─────────────────────────────────────────────────────────────────────

    # ── Global rate limit ────────────────────────────────────────────────
    _check_rate_limit(config.rate_limit_rpm)
    # ─────────────────────────────────────────────────────────────────────

    # Everything below holds the admission slot — release on any unexpected
    # exception that isn't caught by the streaming/error paths.
    try:
        # Resolve real client IP from reverse proxy headers if behind Traefik
        xff = request.headers.get("x-forwarded-for")
        if xff:
            client_host = xff.split(",")[0].strip()
        else:
            client_host = request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")

        from app.upstream import resolve_host
        caller_name = resolve_host(client_host)

        # Build upstream request (applies overrides, resolves auto, hoists extra_body).
        resolved_model, upstream_model = await _build_upstream_body(body, upstream, config, model_name)

        # ── Model readiness guard ────────────────────────────────────────────
        # Avoid triggering conflicting model swaps while llama-swap is already
        # loading a model (e.g. user manually switching via the UI).
        loading = await upstream.get_loading_state()
        if loading:
            loading_model = loading.get("model", "")
            # If the currently-loading model is not the one we need, wait for
            # the ongoing transition to finish before proceeding.
            if loading_model and loading_model != body.get("model"):
                wait_start = time.time()
                await upstream.wait_for_model_ready(loading_model, timeout=120.0)
                waited = time.time() - wait_start
                # After the ongoing swap finishes, check if our model is ready.
                our_ready = await upstream.wait_for_model_ready(
                    body.get("model", ""), timeout=10.0
                )
        # ─────────────────────────────────────────────────────────────────────

        # Forwarding headers (strip hop-by-hop).
        headers = dict(request.headers)
        headers.pop("host", None)
        headers.pop("content-length", None)
        headers.pop("authorization", None)

        # Telemetry entry — populated further after response.
        entry = TelemetryEntry(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(start_time)),
            caller_host=client_host,
            caller_name=caller_name,
            consumer=consumer,
            model=resolved_model,
            upstream_model=body.get("model", ""),
            request_id=request.headers.get("x-request-id", ""),
        )

        # Send upstream request with streaming.
        req = upstream.client.build_request("POST", upstream.api_base, json=body, headers=headers)
        resp = await upstream.client.send(req, stream=True)
        entry.status_code = resp.status_code

        # On error, read body and return immediately.
        if resp.status_code >= 400:
            error_body = await resp.aread()
            error_text = error_body.decode("utf-8", errors="replace")[:500]

            # Retry on 429 (upstream slots full) — exponential backoff, 6 attempts = ~40s total wait.
            if resp.status_code == 429:
                for attempt in range(6):
                    backoff = 1.0 + attempt * 2.5
                    await asyncio.sleep(backoff)
                    req = upstream.client.build_request("POST", upstream.api_base, json=body, headers=headers)
                    resp = await upstream.client.send(req, stream=True)
                    entry.status_code = resp.status_code
                    if resp.status_code < 400:
                        forward_headers = {
                            k: v
                            for k, v in resp.headers.items()
                            if k.lower() in {"content-type", "cache-control", "connection"}
                        }
                        return StreamingResponse(
                            content=_stream_with_release(resp, start_time, entry, telemetry, consumer),
                            status_code=resp.status_code,
                            headers=forward_headers,
                        )
                    error_body = await resp.aread()
                    error_text = error_body.decode("utf-8", errors="replace")[:500]

            # Auto-fallback: if llama-swap can't identify the model, retry with Gemma4-26B-A4B-QAT
            if "no model id could be identified" in error_text and body.get("model") != "Gemma4-26B-A4B-QAT":
                body["model"] = "Gemma4-26B-A4B-QAT"
                req = upstream.client.build_request("POST", upstream.api_base, json=body, headers=headers)
                resp = await upstream.client.send(req, stream=True)
                entry.status_code = resp.status_code
                if resp.status_code < 400:
                    forward_headers = {
                        k: v
                        for k, v in resp.headers.items()
                        if k.lower() in {"content-type", "cache-control", "connection"}
                    }
                    return StreamingResponse(
                        content=_stream_with_release(resp, start_time, entry, telemetry, consumer),
                        status_code=resp.status_code,
                        headers=forward_headers,
                    )

            elapsed = time.time() - start_time
            entry.error = error_text
            entry.latency_ms = round(elapsed * 1000, 1)
            telemetry.log(entry)
            await _release_request(consumer)
            return Response(
                content=error_body,
                status_code=resp.status_code,
                headers=dict(resp.headers),
            )

        # Successful response — wrap in streaming + release handler.
        forward_headers = {
            k: v
            for k, v in resp.headers.items()
            if k.lower() in {"content-type", "cache-control", "connection"}
        }

        return StreamingResponse(
            content=_stream_with_release(resp, start_time, entry, telemetry, consumer),
            status_code=resp.status_code,
            headers=forward_headers,
        )
    except Exception:
        await _release_request(consumer)
        raise

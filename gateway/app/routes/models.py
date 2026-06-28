"""OpenAI-compatible ``/v1/models`` endpoint.

Exposes both:
1. Model aliases configured in ``config.yaml`` (with per-alias overrides).
2. Passthrough model IDs from the upstream backend (llama-swap / llama-cpp)
   that aren't already listed as gateway aliases, enabling consumers to
   discover and use models without a gateway configuration entry.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["models"])


@router.get("/v1/models")
async def list_models(request: Request):
    """List available model aliases and upstream passthrough models.

    Gateway-configured aliases include their ``max_model_len`` /
    ``context_length`` (capped to the active upstream model's context
    window). Additional upstream model IDs that aren't aliased in the
    gateway config are appended as passthrough entries with no overrides.
    """
    config = request.app.state.config
    upstream = request.app.state.upstream
    models = config.models

    active_model = await upstream.get_active_model()
    upstream_ctx = await upstream.get_context_length(active_model) if active_model else None

    data = []

    # 1. Gateway-configured aliases (with per-alias metadata)
    for alias, override in models.items():
        mml = override.max_model_len
        ctx = override.context_length
        if upstream_ctx is not None:
            mml = upstream_ctx if mml <= 0 else min(upstream_ctx, mml)
            ctx = upstream_ctx if ctx <= 0 else min(upstream_ctx, ctx)
        data.append({
            "id": alias,
            "object": "model",
            "created": 1686935002,
            "owned_by": "gateway",
            "max_model_len": mml,
            "context_length": ctx,
        })

    # 2. Upstream passthrough models (not already aliased in gateway config)
    try:
        upstream_ids = await upstream.get_all_models()
        configured_ids = set(models.keys())
        for model_id in upstream_ids:
            if model_id not in configured_ids:
                ctx = upstream_ctx or 8192
                data.append({
                    "id": model_id,
                    "object": "model",
                    "created": 1686935002,
                    "owned_by": "upstream",
                    "max_model_len": ctx,
                    "context_length": ctx,
                })
    except Exception:
        pass  # Upstream unreachable — still return gateway-only list

    return {"object": "list", "data": data}

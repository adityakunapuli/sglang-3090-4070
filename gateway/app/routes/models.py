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
    window). Upstream models not aliased in the gateway config are
    forwarded verbatim — the backend already knows its own context sizes.
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

    # 2. Upstream passthrough models — forward as-is from the backend,
    #    enriched with the upstream context length (llama-swap doesn't
    #    include max_model_len/context_length in /v1/models, so we inject
    #    the value resolved from /running or /props).
    configured_ids = set(models.keys())
    upstream_models = await upstream.get_all_models_full()
    for model in upstream_models:
        model_id = model.get("id")
        if model_id and model_id not in configured_ids:
            if upstream_ctx is not None:
                model["max_model_len"] = upstream_ctx
                model["context_length"] = upstream_ctx
            data.append(model)

    return {"object": "list", "data": data}

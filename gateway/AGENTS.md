# Gateway Service — Agent Reference

**Headless Ubuntu Server** running on `192.168.254.111` — no display/browser; all interaction via SSH.

This service intercepts OpenAI-compatible API requests before routing them to the active LLM backend (`llama-swap`/`llama-cpp`). It provides per-model overrides, auto model resolution, API key management, priority-based admission control, structured telemetry, and a real-time log dashboard.

## Key Files

```
gateway/
├── config.yaml                 # All configuration — restart to reload
├── docker-compose.yml          # Volume-mounts app/, static/, config.yaml
├── Dockerfile                  # Only rebuild when deps change (pip install)
├── requirements.txt
│
├── app/
│   ├── main.py                 # FastAPI app + lifespan (loads config, sets up telemetry)
│   ├── config.py               # YAML → typed dataclasses (GatewayConfig, ModelOverride, etc.)
│   ├── auth.py                 # Bearer token → consumer name lookup (permissive-by-default)
│   ├── upstream.py             # httpx.AsyncClient pool, model resolution, extra_body hoisting
│   ├── telemetry.py            # Structured logging + asyncio.Queue for SSE
│   ├── sse_helpers.py          # SSE formatting + streaming generator
│   └── routes/
│       ├── chat.py             # POST /v1/chat/completions — core proxy + admission gate
│       ├── models.py           # GET /v1/models — merges aliases + upstream passthrough IDs
│       ├── health.py           # GET /health + /ready
│       └── dashboard.py        # GET /, /logs/stream, /logs/json
│
├── static/dashboard.html       # Terminal-style SSE log viewer (no deps, vanilla JS+CSS)
├── tests/
│   ├── test_config.py          # Config loading + validation
│   ├── test_auth.py            # Token → consumer resolution
│   └── test_chat.py            # Chat proxy logic (mock upstream)
│
├── test_endpoints.py           # Integration test (live backend required)
└── test_vision.py              # Vision + structured output end-to-end
```

## Deployment Commands

```bash
# Start / update (no rebuild needed — code is volume-mounted)
cd /mnt/data/docker/gateway && docker compose up -d

# Restart (after code/config changes)
docker compose restart

# Rebuild image (after pip install changes)
docker compose build && docker compose up -d

# Logs
docker compose logs -f

# Shell inside container
docker compose exec gateway bash

# Run unit tests (no backend needed)
python -m pytest tests/ -v
```

## Configuration (config.yaml)

Loaded **once at startup** in `config.py` into `GatewayConfig` dataclass. Restart required for changes.

```yaml
keys:
  allowed_tokens: ["sk-..."]          # Empty list = no auth
  per_consumer:                        # Maps tokens → consumer names (for telemetry)
    frigate: "sk-..."

models:
  alias_name:                          # What the consumer sends in `model:` field
    model: "auto"                      # "auto" = query upstream /running or /v1/models
    temperature: 0.8                   # Override applied before forwarding
    max_tokens: 512
    top_p: 0.9
    extra_body:                        # Keys hoisted to root for llama-server compat
      chat_template_kwargs: { enable_thinking: false }

rate_limit:
  requests_per_minute: -1              # -1 = disabled

qos:
  priorities:
    frigate: 100                       # Lower number = higher. Unlisted → priority 0.
```

## Key Patterns

### Auto Model Resolution
`config.py:_resolve_model()` sends GET `/running` (llama-swap) then `/v1/models` (llama-cpp) to discover the active model. Cached only per-request; no persistent cache.

### Passthrough Models
Any model name not in `config.yaml` routes through **unchanged** — no overrides applied. `/v1/models` (in `routes/models.py`) merges gateway aliases with upstream model IDs, deduplicated by name.

### Admission Control
`routes/chat.py:_admit_request()` checks `qos.priorities` before proxying. If a higher-priority consumer has an inflight request, returns **503** with detail. `_release_request()` is called after stream completion or error. Active requests tracked per-consumer in a dict with counters.

### Extra Body Hoisting
`upstream.py:call_upstream()` moves `extra_body` keys to the payload root for `llama-server` compatibility (e.g. `chat_template_kwargs`).

### Telemetry Colors
`telemetry.py:ColoredFormatter` renders log output with distinct per-metric ANSI colors:
- Tokens → **bold yellow** (`\x1b[93;1m`)
- Tok/s → **bold cyan** (`\x1b[96;1m`)
- TTFT → **bold magenta** (`\x1b[95;1m`)
- Latency → **bold red** (`\x1b[91;1m`)

### Web Dashboard CSS Colors
`static/dashboard.html` uses matching colors for SSE-rendered entries:
- `.tokens` → amber `#d29922`
- `.rate` → blue `#79c0ff`
- `.ttft` → purple `#bc8cff`
- `.latency` → red `#ff7b72`

## Common Modifications

### Adding a consumer
1. Add token to `config.yaml` under `keys.per_consumer`
2. Optionally add a model alias under `models`
3. Optionally set QoS priority under `qos.priorities`
4. `docker compose restart`

### Adding a new endpoint
1. Create `app/routes/<name>.py` with a `router = APIRouter(...)` and `@router.get/post/...`
2. Register in `app/main.py` via `app.include_router(router)`
3. Volume-mount is live — restart only for import-tree changes

### Modifying telemetry fields
Edit `TelemetryEntry` dataclass in `telemetry.py`, then update `LogEntryFormatter.format()` in the same file. The SSE queue sends `asdict(entry)` so new fields appear in the dashboard automatically.

## Tests

- **Unit tests** (`tests/`) use mocked upstream, no GPU/backend needed — run locally
- **Integration tests** (`test_endpoints.py`, `test_vision.py`) require live backend — run on server only
- `conftest.py` provides a `test_client` fixture with a configured Gateway app

## Gotchas

- No `uvicorn --reload` — code is volume-mounted but needs `docker compose restart` for import-tree/config changes
- `config.yaml` changes always need a restart (loaded once in lifespan)
- `static/dashboard.html` changes are picked up on browser refresh (served fresh per request, `Cache-Control: no-cache`)
- Web UI uses SSE — the stream endpoint sends initial replay of last 100 entries, then real-time updates with 15s heartbeat
- QOS admission is purely in-memory, per-process — no shared state between replicas

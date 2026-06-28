# LLM Gateway

A lightweight OpenAI-compatible proxy that sits in front of **llama-swap** (or **llama-cpp**), providing per-consumer
access control, model aliasing with parameter overrides, priority-based admission control, structured telemetry, and a
real-time log dashboard.

## Architecture

```
[Consumer] ──POST /v1/chat/completions──▶ [Gateway :4001]
                                               │
                         ┌─────────────────────┼─────────────────────┐
                         │                     │                     │
                    GET /v1/models        POST /v1/chat/        GET / (dashboard)
                    (aliases + upstream    completions           SSE log stream
                     passthrough models)   (proxied + overrides)
                         │                     │
                         └─────────┬───────────┘
                                   │
                        ┌──────────▼──────────┐
                        │  llama-swap :8082    │
                        │  or llama-cpp        │
                        └─────────────────────┘
```

## Quick Start

```bash
cd /mnt/data/docker/gateway

# Build (only when dependencies change)
docker compose build

# Start
docker compose up -d

# Check logs
docker compose logs -f
```

The gateway listens on `http://0.0.0.0:4000` inside the container and is exposed on **`http://192.168.254.111:4001`**.

## Configuration

All configuration lives in `config.yaml`. The file is loaded once at startup; changes require a restart.

### API Key Management

**Permissive-by-default:** if no `allowed_tokens` are listed, all requests pass through.

```yaml
keys:
  allowed_tokens:
    - "sk-password"
  per_consumer:
    frigate: "sk-password"
    hermes: "local-no-key"
    opencode: "opencode-key"
```

The `per_consumer` map assigns human-readable consumer names to tokens so telemetry identifies who made each request.

### Model Overrides

Each entry maps a model alias (what the consumer sends) to overrides applied before forwarding to the upstream.

```yaml
models:
  frigate:
    model: "Qwen3.6-27B"          # Upstream model name (or "auto")
    temperature: 0.8
    max_tokens: 512
    extra_body:
      chat_template_kwargs: { enable_thinking: false }

  ocr:
    model: "auto"                  # Resolves to the currently active upstream model
    top_p: 0.1
    temperature: 0.0
```

**`model: "auto"`** queries the upstream's `/running` (llama-swap) or `/v1/models` (llama-cpp) to dynamically route to
the active model at request time.

### Passthrough Models

Any model name not in `config.yaml` passes through **unchanged** with no overrides applied. This lets consumers directly
address llama-swap models (e.g. `Gemma-4-12B-v2-Agentic`) without a gateway configuration entry.

The `/v1/models` endpoint returns **both** gateway aliases and upstream passthrough model IDs, so consumers can discover
everything available.

### Rate Limiting

```yaml
rate_limit:
  requests_per_minute: -1   # -1 disables, N enforces a global sliding-window limit
```

### QoS — Priority Admission Control

Prevents low-priority traffic from interfering with interactive consumers. When a higher-priority consumer has an active
in-flight request, lower-priority requests are rejected with **HTTP 503**.

```yaml
qos:
  priorities:
    frigate: 100           # Lower number = higher priority. Unknown → 0.
```

Consumers not listed default to priority **0** (highest). Frigate (priority 100) gets 503'd whenever any non-Frigate
consumer has an active request. Frigate's OpenAI client automatically retries on 503.

## Endpoints

| Path                   | Method | Description                                          |
|------------------------|--------|------------------------------------------------------|
| `/health`              | GET    | Liveness probe                                       |
| `/ready`               | GET    | Readiness probe                                      |
| `/v1/models`           | GET    | List models (gateway aliases + upstream passthrough) |
| `/v1/chat/completions` | POST   | Proxy to upstream with overrides & admission control |
| `/`                    | GET    | Live log dashboard                                   |
| `/logs/stream`         | GET    | SSE stream of telemetry entries                      |
| `/logs/json`           | GET    | Recent telemetry as JSON (?count=N)                  |

## Telemetry

Each request produces a JSON line in `logs/gateway.json` with:

| Field                                 | Description                  |
|---------------------------------------|------------------------------|
| `consumer`                            | Authenticated consumer name  |
| `model`                               | Resolved model alias         |
| `upstream_model`                      | Actual model sent to backend |
| `prompt_tokens` / `completion_tokens` | Token usage                  |
| `tokens_per_sec`                      | Generation throughput        |
| `ttft_ms`                             | Time to first token (ms)     |
| `latency_ms`                          | Total request latency (ms)   |
| `status_code`                         | HTTP response status         |
| `error`                               | Error message (if any)       |

### Docker Logs

The terminal output uses colour-coded metrics for quick visual scanning:

- `tokens=1203↗135` — **amber** (prompt → completion)
- `tok/s=16.20` — **blue**
- `ttft=8.33s` — **purple**
- `total=8.33s` — **red**

### Web Dashboard

Open `http://192.168.254.111:4001/` in a browser for a terminal-style log viewer with real-time SSE updates,
colour-coded entries, pause/clear controls, an error-only filter, and collapsible request detail panes.

## Development

```bash
# Run tests
python -m pytest tests/ -v

# Integration tests (live backend required)
uv run test_endpoints.py
uv run test_vision.py
```

Code is volume-mounted at runtime (`./app:/app/app`, `./static:/app/static`, `./config.yaml:/app/config.yaml`). Restart
the container to pick up changes:

```bash
docker compose restart
```

## Credits

Favicon by **Trend Icons** from [Noun Project](https://thenounproject.com/icon/y-intersection-4929746/).

Only rebuild when Python dependencies change:

```bash
docker compose build
docker compose up -d
```

# Project Overview

This workspace is a **Service-First (Modular) Home Server Infrastructure**.

Unlike monolithic setups, this project is designed around **service autonomy**. Every directory is an independent,
self-contained Docker Compose stack. This architecture ensures that a failure or configuration change in one service
(e.g., an LLM engine) does not impact the stability of others (e.g., DNS or Media).

## Architecture & Structure

The infrastructure is decentralized. There is **no root orchestrator**.

- **Modular Stacks:** Services are grouped into logical directories (e.g., `arr-stack`, `immich`, `monitoring`,
  `openwebui`).
- **Scratch Space:** The `.archive/` directory is a git-ignored scratch space reserved for creating test scripts,
  sharing screenshots, and transient experimentation.
- **Global Ingress:** A shared `proxy` network (managed by Traefik, which resides in its own isolated directory) acts as
  the unified gateway for all web-facing services.
- **Shared Secrets:** A centralized `.env` file in the root directory provides consistent API keys, paths, and hardware
  UUIDs across multiple stacks via `env_file: ../.env`.
- **Inter-Service Communication:** Services connect to the `proxy` network for external access and use internal
  `default` networks for stack-specific communication.

## Directory Structure (Depth 2)

### Root level (`/mnt/data/`)

```
/mnt/data/
├── agents/                    # Hermes agent runtime (logs, state, skills, cron)
│   ├── hermes/
│   │   ├── logs/              # agent.log, errors.log, gateway logs
│   │   ├── state.db           # Session database
│   │   ├── skills/            # Installed skills
│   │   ├── cron/              # Scheduled jobs
│   │   └── memories/          # Persistent memory (USER.md)
│   └── haos/                  # Home Assistant OS image (haos.qcow2)
├── docker/                    # All Docker Compose stacks (see below)
└── media_cache/               # Cached media server data
    ├── jellyfin/              # Jellyfin DB + logs
    └── plex/                  # Plex config, cache, transcodes, logs
```

### Docker stacks (`/mnt/data/docker/`)

```
/mnt/data/docker/
├── AGENTS.md                  # This file — infrastructure reference
├── GPUS.md                    # GPU UUID registry
├── .env                       # Shared secrets (API keys, paths, hardware UUIDs)
├── .gitignore
├── genai.env                  # AI service env (LiteLLM refs, Frigate model config)
│
├── arr-stack/                 # Radarr, Sonarr, Prowlarr, Lidarr, etc.
│   └── docker-compose.yml
├── audiobookshelf/            # Audiobook/audiobookshelf media server
│   ├── docker-compose.yaml
│   ├── .env
│   └── libation_config/       # Libation (audible downloader) config
├── coolercontrol/             # GPU/PC hardware monitoring & fan control
│   ├── docker-compose.yml
│   └── config/                # config.toml, config-ui.json, alerts
├── dockhand/                  # Docker container management dashboard
│   └── docker-compose.yaml
├── frigate/                   # NVR / CCTV with AI object detection
│   ├── docker-compose.yml
│   ├── frigate_schema.json
│   ├── update_frigate_compose.py
│   └── config/                # frigate.db, go2rtc_homekit.yml, search_stats
├── gateway/                   # STOPPED legacy LLM gateway (rollback path only)
│   ├── docker-compose.yml
│   ├── config.yaml            # Per-model overrides, API keys, QoS, rate limiting
│   ├── app/                   # Python app: config.py, routes/models.py, upstream.py
│   └── tests/                 # test_auth.py, test_chat.py, test_config.py, conftest.py
├── homepage/                  # Unified dashboard (port 3000) + Glances (61208)
│   ├── docker-compose.yaml
│   └── public/                # Icons (hermesagent, openwebui, qwen, llama, etc.)
├── hermes/                    # AI Agent (ports 8642, 9119, 8767, 8787)
│   ├── docker-compose.yml
│   ├── .env
│   ├── Dockerfile.relay         # Custom relay image build
│   └── scripts/                 # (none currently)
├── immich/                    # Photo/video management
│   ├── docker-compose.yaml
│   ├── .env
│   ├── .env.SAMPLE
│   ├── hwaccel.transcoding.yml
│   ├── immich-progress-watcher.sh
│   ├── schema.txt
│   └── system_settings_import.json
├── jellyfin/                  # Media streaming (port 8096, GPU: GPU_SLOT_3)
│   └── docker-compose.yml
├── journiv/                   # Journaling/writing app
│   ├── docker-compose.yml
│   └── .env.SAMPLE
├── llama-cpp/                 # Standalone llama.cpp server (port 8082:8082)
│   ├── docker-compose.yaml
│   ├── models.ini             # Model presets (GLM-OCR active, others commented out)
│   ├── docs/                  # speculative.md, multigpu.md, llama-server.md
│   └── tests/                 # test_endpoints.py, test_vision.py, results.jsonl
├── llama-swap/                # Hot-swappable LLM model manager (ports 5800, 8082)
│   ├── docker-compose.yaml
│   ├── configs/               # Per-model llama-swap definitions (what the container reads)
│   └── llama-swap-config.yaml # Historical single-file config; NOT on the --config-dir path
├── litellm/                   # LLM FRONT DOOR (port 4001) — the single gateway
│   ├── docker-compose.yml
│   ├── config.yaml            # model_list: the `*` passthrough + TTS. Restart to reload
│   ├── aliases.yaml           # Routing aliases (frigate/ocr/analysis/...) + their overrides
│   ├── custom_auth.py         # Permissive tagging auth; accepts ANY token
│   ├── models_middleware.py   # /v1/models + alias resolution + reasoning_effort translation
│   ├── sitecustomize.py       # Installs the middleware without forking LiteLLM
│   ├── consumer_tokens.json   # token -> consumer name (telemetry labels)
│   └── verify_front_door.sh   # 16-check smoke test; run after any change here
├── monitoring/                # System & GPU telemetry (Telegraf/Prometheus/Grafana)
│   ├── docker-compose.yml
│   ├── prometheus.yml         # llm-backend (:8082) + llamacpp (metrics bridge) scrape jobs
│   ├── metrics_proxy.py       # llamacpp bridge: dynamic llama-server port + /slots-derived KV
│   ├── vuegraf.json
│   ├── grafana/               # provisioning: dashboards, datasources
│   │                          # "LLM Inference" charts TTFT, decode/prefill, KV cache
│   ├── telegraf/              # telegraf.conf
│   └── vram_temps/            # Custom VRAM exporter (C source, Nix flake, build scripts)
├── openrgb/                   # RGB lighting control (port 15800:5800, 6742:6742)
│   ├── docker-compose.yml
│   ├── Dockerfile.gui
│   └── startapp.sh
├── openwebui/                 # Web UI for LLM chat (port 3030:8080)
│   ├── docker-compose.yaml
│   └── tests/                 # test_api.py, test_api.sh
├── paperless-ngx/             # Document management + AI OCR
│   ├── AGENTS.md              # Stack-specific reference
│   ├── docker-compose.yml     # 6 services: webserver, db, broker, gotenberg, tika, ocr-sidecar
│   ├── .env
│   ├── ai-system-prompt.txt   # Legacy system prompt (not used by sidecar)
│   ├── ocr-sidecar/           # Custom LLM-based OCR + AI metadata extraction
│   │   ├── app/               # main.py, config.py, ocr.py, ai.py, paperless.py, database.py
│   │   ├── prompts/           # analysis.md — AI analysis prompt
│   │   ├── static/            # Web UI for monitoring
│   │   ├── Dockerfile
│   │   └── requirements.txt
│   ├── surya-ocr/             # UNUSED: Old Surya-OCR sidecar (built but not in compose)
│   ├── paperless-ai-data/     # DEFUNCT: Old PaperlessGPT sidecar
│   ├── paperless-ai-next-data/# DEFUNCT: Paperless-AI-Next sidecar
│   └── gpt-data/              # DEFUNCT: Legacy GPT integration
├── plex/                      # Media streaming (port 32400:32400)
│   ├── docker-compose.yml
│   ├── README.md
│   └── Preferences.xml
├── scrutiny/                  # Drive health monitoring (S.M.A.R.T.)
│   └── docker-compose.yml     # Scrutiny (port 8080:8080, 8086:8086)
├── scripts/                   # Shared utility scripts
│   └── docs/                  # Timestamped docs (20260628-*.py, *.md)
├── sglang/                    # Primary SGLang backend — Swift-1.5 Qwen3.8-27B (PP2, port 8082)
│   ├── docker-compose.yml     # pinned nightly; EAGLE/MTP toggle via SGLANG_SPEC_FLAGS
│   ├── .env                   # stack config (git-ignored)
│   ├── patches/               # bind-mounted qwen3_5 mm-relay + MTP PP-spec patches
│   ├── docs/                  # migration + benchmark log (20261002-*-sglang-qwen38-migration.md)
│   └── tests/                 # bench.py, needle.py, results/
├── whisper-cpp/               # whisper-cpp (STT, port 8084) & audio-cpp (TTS Kokoro, port 8085)
│   ├── docker-compose.yaml
│   ├── README.md
│   └── audio-cpp/             # Dockerfile & server.json for Kokoro 82M TTS
└── wud/                       # What's Up Docker (update notifications)
    └── docker-compose.yml
```

**Network topology:** All stacks connect to the external `proxy` network for Traefik ingress. Each stack also has its own
`default` network for internal communication. The monitoring stack uses a separate `monitoring` network.

## Key Technologies

- **Orchestration:** Docker Compose (Independent per stack)
- **Ingress/Proxy:** Traefik (External network: `proxy`)
- **Dashboard:** Homepage (Unified view of all independent services)
- **Monitoring:** Telegraf/InfluxDB/Grafana (Service-agnostic monitoring)
- **AI Stack:** LiteLLM (front door), vLLM/SGLang, hyperqwen, llama-swap, llama-cpp, Open WebUI
- **Media Stack:** Plex, Audiobookshelf, Sonarr, Radarr, Prowlarr
- **Updates:** WUD (What's Up Docker) monitoring the local socket for all stacks

---

## ML Service Topology

There is a **bifurcated** LLM routing architecture with multiple "gateways" and inconsistent consumer connectivity. The
goal is to consolidate into a single unified gateway. Below is the current catalog.

### ML Hosting Backends (Model Servers)

These are the actual inference engines that load models onto GPU and serve OpenAI-compatible APIs.

**Every swappable backend publishes host port 8082 AND listens on container-internal port 8082, and claims the
`llm-active` network alias on `proxy`.** This uniformity is load-bearing: the front door resolves backends by
alias, and a network alias cannot carry a port. Only one backend runs at a time (one model is GPU-resident), so
swapping models is a `docker compose up -d` in a different directory — with no front-door config change at all.

| Service        | Directory     | Port(s)              | GPU            | Models Served                                                                       | Role                                                                                                                                     |
|----------------|---------------|----------------------|----------------|-------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------|
| **llama-swap** | `llama-swap/` | 8082:8082, 5800:5800 | 3090 Ti + 4070 Ti S | Qwen3.8-27B, Qwen3.6-36B-A3B-MTP, Gemma4-31B-QAT, Gemma4-26B-A4B-QAT, Nemotron-3.5-Lightning-30B-A3B, Muse-Glimmer-30B (per `configs/`) | Hot-swappable model manager; loads on request, `ttl: 300`. Emits native `meta.llamaswap` blocks |
| **llama-cpp**  | `llama-cpp/`  | 8082:8082            | 3090 Ti + 4070 Ti S | Per `models.ini` presets                                                          | Standalone llama-server router mode. Mutually exclusive with llama-swap (same port + alias)                                          |
| **hyperqwen**  | `hyperqwen/`  | 8082:8082            | RTX 3090 Ti    | Qwen3.8-27B (Ar4ikov AWQ uncensored, patched vLLM 0.30, MTP)                   | Fastest single-stream on the 3090 Ti. `restart: "no"` — it takes the whole card. **Decode currently ~34 tok/s, ~3x below the documented 101; see `scripts/docs/20260930-hyperqwen-decode-perf-investigation.md`** |
| **vLLM/SGLang**| `vllm/`       | 8082:8082            | 3090 Ti (+4070 Ti S) | Qwen3.6-35B-A3B-AWQ, Gemma-4-26B-A4B-AWQ, Nemotron (SGLang) — compose profiles `qwen`/`gemma`/`nemotron` | Experimental; mutually exclusive profiles, all on 8082                                       |
| **sglang**     | `sglang/`     | 8082:8082            | 3090 Ti + 4070 Ti S  | Swift-1.5-Qwen3.8-27B-AWQ-INT4 (vision + MTP in checkpoint) — **PP2** across both cards          | Primary sglang stack; nightly image pinned. MTP **ON and working** (bf16-draft fix, decode 75-84 tok/s, pool ~167k; no-spec fallback via `SGLANG_ENABLE_PP_SPEC=0`). Mutually exclusive with the other 8082 backends |
| **whisper-cpp**| `whisper-cpp/`| 8084:8080            | RTX 4070 Ti S  | whisper-large-v3-turbo-q4_k                                                        | Speech-to-text. Native `POST /inference` only — bridged to OpenAI `/v1/audio/transcriptions` by the front door |
| **audio-cpp**  | `whisper-cpp/`| 8085:8080            | RTX 4070 Ti S  | kokoro-82m-bf16                                                                     | Text-to-speech; natively OpenAI `/v1/audio/speech`                                            |
| **gemma-ha**   | `hyperqwen/`  | 8072:8080            | RTX 4070 Ti S  | Gemma-4-12B-MTP (llama.cpp)                                                        | Always-on Home Assistant assist model; runs alongside a main backend                             |

### Front Door (Gateway / Proxy Layer)

**There is exactly one: LiteLLM.** It replaced the hand-written `gateway/` (FastAPI) service, which is stopped
but retained as a rollback path. Bifrost was evaluated and rejected — it cannot pin per-alias default params
server-side (they would need a request header from every consumer) and its model catalog goes stale across
model swaps. Neither is deployed.

| Service   | Port | Backend Target                          | Features                                                                                                                                                     |
|-----------|------|-----------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **LiteLLM** | 4001 | `http://llm-active:8082/v1` (alias)   | Single stable address; `model_name: "*"` verbatim pass-through; alias resolution + per-alias param overrides; permissive tagging auth; `/v1/models` with `meta.llamaswap`; STT/TTS proxy; spend logs in the central Postgres |

Container port 4000; host port **4001** so the direct-IP consumers (Hermes, Agent Zero) needed no change. The
container also carries the network alias **`gateway`** so the Docker-DNS consumers (`gateway:4000/v1`) needed
no change either. **No consumer configuration was modified during the migration.**

Postgres lives in the central `database/` stack (`db` container, database `litellmdb`, role `litellm`) — the
old `litellm/` stack's private postgres and its broken `.env` were removed.

### Consumer Services (Downstream LLM Clients)

These services send chat/completion requests to the hosting layer.

| Consumer                | Connects To (URL)                | Method                          | Model Alias           | Notes                                                                                                                             |
|-------------------------|----------------------------------|---------------------------------|-----------------------|-----------------------------------------------------------------------------------------------------------------------------------|
| **Frigate**             | `http://gateway:4000/v1`         | Docker DNS via `proxy` network  | `frigate`             | Alias resolved to the live model; thinking disabled server-side. No config change in the migration.                               |
| **Paperless-ngx (OCR)** | `http://gateway:4000/v1/chat/completions` | Docker DNS via `proxy`  | `ocr`                 | `paperless-ngx/ocr-sidecar`. Thinking disabled, temp 0.                                                                             |
| **Paperless-ngx (AI)**  | `http://gateway:4000/v1/chat/completions` | Docker DNS via `proxy`  | `analysis`            | Same sidecar, separate channel.                                                                                                     |
| **postgres-mcp**        | `http://gateway:4000/v1`         | Docker DNS via `proxy` network  | `default`             | Database stack.                                                                                                                      |
| **wyoming-openai (STT)**| `http://gateway:4000/v1`         | Docker DNS via `proxy` network  | `whisper-1`           | Bridged to whisper-cpp's native `/inference`.                                                                                        |
| **wyoming-openai (TTS)**| `http://audio-cpp:8080/v1`      | Direct backend                  | `kokoro`              | TTS left direct; audio-cpp already speaks the OpenAI route.                                                                         |
| **Hermes Agent**        | `http://192.168.254.111:4001`   | Direct IP (host port)           | Default               | Unchanged: LiteLLM publishes 4001 deliberately. Note the missing `/v1` suffix — hermes joins URLs itself.                            |
| **Agent Zero**          | `http://192.168.254.111:4001/v1`| Direct IP (host port)          | `default`, `Nemotron-*`, `haos-gateway-model` | 6 presets, all unchanged.                                                                                              |
| **Open WebUI**          | `http://gateway:4000/v1`         | Docker DNS via `proxy` network  | Generic               | Previously a dead `litellm:4000` link; now resolves.                                                                               |
| **opencode / pi**       | `http://192.168.254.111:4001`   | Host-side clients               | Varies                | Config lives outside this repo — update if still pointing at the old gateway.                                                       |

### Network Topology Diagram

```
                           PROXY NETWORK (external)
      ==================================================================
      |         |          |         |         |         |         |
   [LiteLLM] [llama-    [llama-  [hyper-  [vLLM/   [Frigate] [Paperless]
    :4001/    swap]      cpp]     qwen]    SGLang]  :5000    [ocr]
    :4000      [all also claim the `llm-active` alias on :8082]
      |        [gemma-ha :8072]
      |
      +--- consumers resolving `gateway` (unchanged) -------------------+
      |    Frigate, Paperless OCR+AI, Open WebUI, postgres-mcp,       |
      |    wyoming-openai (STT)                                      |
      +---------------------------------------------------------------+
      |
      +----[ LAN Host 192.168.254.111 ]----(for direct port access)
      |      Hermes + Agent Zero address :4001 directly; no change.
      |
      +----[ whisper-cpp :8080 ]  STT   (native /inference, bridged)
      +----[ audio-cpp   :8080 ]  TTS   (native /v1/audio/speech)

      HERMES NETWORK (hermes_net, internal only)
      ==================================================================
      |            |            |
      |  [hermes]  | [hermes-] | [hermes-]
      |  (hermes)  | [mobile]  | [relay]
      |            |            |
      |  :8642 API |  :8787 UI  |  :8767 WSS
      |  :9119 Dash|            |  (Android app)
      |            |            |
      +------------+------------+
      |
      [Hermes data: /mnt/data/.agents/hermes (bind-mounted)]

      [QwenPaw] - host network (no container networking)
      [Immich] - proxy network (no LLM dependency, uses own ML pipeline)
```

### Consolidation: complete (2026-09-29)

The bifurcation is resolved. There is now **one front door** (LiteLLM, host port 4001) and **one address pattern
per connectivity class**, unchanged from what consumers already had.

What replaced what:

| Was | Now |
|---|---|
| `gateway/` (hand-written FastAPI, 4001) | Stopped, retained as a rollback path. Replaced by `litellm/`. |
| `bifrost/` (referenced, never deployed) | Removed from the topology. Evaluated and rejected — see below. |
| `litellm/` (present but broken) | Repaired and deployed. |

**Why not Bifrost.** It is the stronger product on governance and telemetry, but two blockers land on this
setup's priorities: (1) per-alias default params are not expressible server-side — extra params require an
`x-bf-passthrough-extra-params` header on *every request*, which Frigate and Paperless cannot be made to send;
(2) bare unknown model names fail, because the model catalog is built from each provider's `/v1/models` at
startup and goes stale every time a model is hot-swapped. LiteLLM's `model_name: "*"` is a genuine
first-class wildcard, and its `extra_body` merges server-side.

**Design decisions worth knowing:**

- **Alias resolution must work for BOTH backend families.** `active_model()` resolves, in order:
  (1) llama-swap `/running`, (2) llama.cpp `status.value == "loaded"`, (3) *a vLLM backend, which
  exposes neither — so if the backend advertises exactly one non-alias model, that is the live one*,
  (4) the `default_model` in `aliases.yaml`. Step 3 is load-bearing: without it every alias fell
  through to the configured default and 404'd with `The model 'Nemotron-…' does not exist. Served
  models: Qwen3.8-27B`, breaking Frigate and Paperless whenever a vLLM backend held `:8082`.
  The old gateway never had this problem because it hardcoded `model: "auto"` and let the backend
  decide.
- **One model at a time, so the front door is thin.** No load balancing, no model groups, no fallbacks. The
  backend owns model selection; LiteLLM owns the socket, consumer tags, and pass-through.
- **Swapping models needs no front-door change.** Every backend publishes host port 8082, listens on
  container-internal 8082, and claims the `llm-active` network alias. Verified that Docker deregisters the
  alias the moment a container stops, so a stopped backend leaves no stale DNS entry.
- **Auth is permissive by design, matching the old gateway.** `custom_auth.py` accepts *any* token and tags
  it; it rejects nothing. API keys are telemetry labels, not credentials.
- **`/v1/models` and the admin model endpoints are served by `models_middleware.py`, not LiteLLM.**
  The `*` wildcard makes LiteLLM expand the entire OpenAI catalog into its own listings: `/v1/models`,
  `/model_group/info` and `/v1/model/info` each returned ~200 phantom `gpt-*` models and none of the
  real ones. That is also why the admin UI's **Models and Endpoints** table appeared empty — it is
  rendered from `/model_group/info`. The middleware now serves all three from config aliases + the
  live backend, with `meta.llamaswap` blocks and effort ladders that QwenPaw's `VariantParser`
  requires.
- **Models are deliberately NOT stored in the DB.** `store_model_in_db` stays false so config.yaml
  remains the single source of truth; the UI is fed by the middleware instead. Enabling it would let
  DB values silently overlay `general_settings`/`router_settings` on restart, which is a footgun
  documented upstream. (Verified it would not have fixed the empty UI either: `/v2/model/info` reads
  the router's `model_list`, not the DB.)
- **The UI's model table renders from `/v2/model/info`**, not `/model_group/info` — confirmed from the
  compiled client bundle. That endpoint was serving nothing because a viewer role with no `user_id`
  fails `_filter_models_to_user_accessible`, so every deployment was filtered out. `custom_auth.py`
  now returns `PROXY_ADMIN` and the middleware also serves `/v2/model/info` (paginated), so the table
  agrees with `/v1/models` regardless of caller.
- **Auth is deliberately wide open.** This is a single-tenant homeserver; `custom_auth.py` accepts any
  token and tags it, `require_auth_for_metrics_endpoint` is off, and the Prometheus port carries no
  auth. Simplicity over threat-modelling an only-consumer LAN.
- **Variants are hidden from model listings.** `/v1/models` lists each model once, with a
  `reasoning_efforts[]` ladder, instead of one row per effort. Set `MODELS_LIST_INCLUDE_VARIANTS=1`
  in `litellm.env` to restore the old rows. QwenPaw is unaffected either way: its `VariantParser`
  (`VariantParser.kt:54`) builds the ladder from the BASE entry's `meta.llamaswap.aliases[]` and only
  falls back to the top-level `:variant` rows. `Model:low` requests still route correctly.
- **Chat sessions group only if the client sends an id.** With none, LiteLLM mints a fresh uuid4 per
  request, so every turn becomes its own log row. `general_settings.missing_session_id: generate` does
  NOT fix this — it also generates per request. Open WebUI is configured with
  `ENABLE_FORWARD_USER_INFO_HEADERS=True` + `FORWARD_SESSION_INFO_HEADER_CHAT_ID=x-litellm-session-id`;
  the default header name it emits (`X-OpenWebUI-Chat-Id`) does not match LiteLLM's
  `^x-.+-session-id$` pattern, which is why the rename is required.
- **Reasoning effort replaced llama-swap model variants.** llama-swap exposed effort as model-name
  variants (`Qwen3.8-27B:low`) via `filters.setParamsByID`. The front door now owns that mapping: send
  the standard `reasoning_effort` field (or keep using `:low`) and it is translated to the same
  server-side `chat_template_kwargs`. `/v1/models` advertises `reasoning_efforts[]` per base model.
  The `:variant` entries are still listed because QwenPaw's `VariantParser` requires them.
- **Telemetry comes from the backends, not the gateway.** This is a direct migration of the old
  gateway's TTFT/tok-s dashboard onto native backend metrics:
  - vLLM-derived backends expose `vllm:*` on host port 8082 → Prometheus job `llm-backend`.
  - llama.cpp backends expose only host gauges (`llamaswap_*`) on 8082. The inference metrics
    (`llamacpp:*`) come from the llama-server process behind llama-swap, on a port llama-swap
    allocates **dynamically** (5801, 5802, …), so it cannot be a static target. The
    `llamacpp-metrics-proxy` service in `monitoring/` discovers the live port via llama-swap's
    `/running`, merges both families, and adds the `model` label llama.cpp omits → job `llamacpp`.
  - **TTFT comes from LiteLLM itself**, on host port 4002 (Prometheus job `litellm`):
    `litellm_llm_api_time_to_first_token_metric` is a real histogram and the only true TTFT on this
    box — it includes queueing and slot wait that server-side timestamps would exclude. Enabled with
    `litellm_settings.callbacks: ["prometheus"]` + `--prometheus_metrics_port 4002`. It fires for
    STREAMING requests only, so Frigate and Paperless never appear (both non-streaming by design).
  - **GPU panels come from nvitop-exporter, not llama-swap.** Reading `llamaswap_gpu_*` made them
    blank whenever a non-llama-swap backend held `:8082`. Note also that nvitop's
    `gpu_memory_utilization_Percentage` is **wrong** on this box (reported 35% where nvidia-smi
    said 93%); its absolute `gpu_memory_used_MiB` matches exactly, so the panel computes
    `used/total` itself.
  - Grafana's "LLM Inference" dashboard charts all three. Exactly one backend family is live at a
    time, so one row is populated and the others are blank by design.
  - **Never plot `llamacpp:*_tokens_seconds` for throughput.** Those gauges are an average over the
    window between two scrapes and the server RESETS the bucket on every read
    (`server-context.cpp:4665`), so a 5s Prometheus scrape samples a sliver and routinely reads 0
    while a request is actively decoding. `n_per_second()` also returns *steps*/time, and for decode
    `steps != tokens` under speculative decoding (`server-common.h:471`). Derive instead from the
    cumulative counters, which is what matches the server's own log:
    `rate(prompt_tokens_total)/rate(prompt_seconds_total)` and
    `rate(tokens_predicted_total)/rate(tokens_predicted_seconds_total)`.
    Verified: counters give ~830 tok/s prefill and ~45 tok/s decode against logged 623–1314 and
    35.9–52.9, while the gauge read 0 during active decoding.
  - **`timings.prompt_per_second` excludes CACHED prompt tokens** (`prompt_n` excludes `cache_n`), so
    on a cache hit a 40k-token prompt legitimately reports ~20 tok/s. Do not use it for a cache-
    independent prefill panel. Note the prompt cache hit rate here is ~90%, so most of a long agent
    prompt is never re-decoded.
  - **Prefill throughput is 2500-4000 tok/s, and that is NOT a bug.** This box runs a *sparse MoE*
  (`Qwen3.6-35B-A3B` = 35B total, **3B active**), so prefill only evaluates 3B of parameters per
  token, and llama-spp's batched ubatches amortise the expert weight reads. Measured ~99 TFLOPS
  effective, which the 3090 Ti + 4070 Ti S can genuinely deliver on a Q4_K dequant path. Do not
  compare this against a dense 35B log line (that is the ~1300 tok/s figure) — they are different
  amounts of arithmetic. Prefill rate also falls off with prompt length (O(n^2) attention): measured
  4098 tok/s @14k, 3556 @60k, 2536 @155k.
- **The two GPUs are on separate PCIe root complexes.** `nvidia-smi topo -m` reports `PHB` between
  GPU0 (3090 Ti) and GPU1 (4070 Ti S), so layer-split traffic crosses the host bridge. This is the
  "bottlenecked by interconnect" case in `docs/multi-gpu.md`. NCCL 2.30.7 IS present, so this is not
  a missing-NCCL degradation.
- **Tuned `--tensor-split` for this pair: `65,35`, not `70,30`.** Benchmarked with fixed ~30k-token
  uncached prompts (3-5 runs each): 70,30 = 3219 tok/s; **65,35 = 3412 tok/s (+6%)**; 60,40 = 3508
  (+9%) but leaves only 794 MiB on GPU1; 80,20 = 2842 (-12%, and GPU0 hits 92% VRAM). The 4070 Ti S
  is the faster card per layer and was under-used at 30%, so shifting layers *toward* it wins; pushing
  too far either way costs VRAM headroom. 65,35 is the best speed/safety point because GPU1 also
  hosts jellyfin + audiocpp + whisper (~2.6 GB) that must not be evicted.
- **KV cache is quantised (`q8_0`) with `--cache-reuse 256` on this model.** Throughput-neutral but it
  frees ~1.5 GB, which is what makes the 65,35 split safe, and cache reuse is a real win for the
  repeated-prefix agent workloads. Needs `--flash-attn on` (already set).
- **`--split-mode` accepts only `none|layer|tensor` in this build — `row` is DEPRECATED and hard-fails**
  ("device CUDA0 does not support split buffers"). Do not try it. `layer` (the default) is the correct
  choice here: it is documented as the one that maximises prefill/batch throughput. `tensor` is
  experimental, targets generation latency rather than prefill, requires NON-quantised KV
  (conflicts with the `q8_0` above), and is not implemented for every architecture.
- **Prefill and decode differ by ~20x on this box** — plot them on separate axes, or decode
    flattens to an unreadable line against a 1000 tok/s prefill.
  - **`--metrics` is required** on every llama-server cmd. It is present in all
    `llama-swap/configs/*.yaml`; without it llama-server returns HTTP 501 and the panel reads OFF.
  - **llama.cpp telemetry is per-response, not aggregate.** Every completion carries a `timings`
    object (`server_slot_stats::to_json()`, `tools/server/server-common.cpp:78`) with 11 fields —
    `cache_n`, `prompt_n`, `prompt_ms`, `prompt_per_token_ms`, `prompt_per_second`, `predicted_n`,
    `predicted_ms`, `predicted_per_token_ms`, `predicted_per_second`, and conditional `draft_n` /
    `draft_n_accepted`. It arrives in the non-streaming body and in the final SSE chunk (every chunk
    with `timings_per_token: true`). **That is exactly what llama-ui renders** — the token count,
    duration and t/s under each assistant message. There is no metrics-dashboard concept in llama-ui.
  - **TTFT is not computed anywhere in llama-server.** No metric, no body field, no header. The
    server's internal `t_gen_last` (which would give it) is not exported. `bench/script.js` measures
    it client-side, timing the first SSE delta. So a real TTFT *time series* must be measured at the
    front door, not scraped. Do not label prefill time as TTFT.
  - **No direct KV/context metric.** `llamacpp:n_tokens_max` is a high-water mark since load, not
    utilisation. Current usage is derived from `/slots`:
    `context_used = Σ(n_prompt_tokens + next_token[0].n_decoded)`, `context_total = Σ(n_ctx)`.
    Note `n_past` does not exist in this build.

## Operational Procedures

### Managing Services

Since there is no central `docker-compose.yml`, all commands must be run within the specific service directory:

- **Start/Update Service:** `cd <service-dir> && docker compose up -d`
- **Stop Service:** `cd <service-dir> && docker compose down`
- **Check Logs:** `cd <service-dir> && docker compose logs -f`

### Hermes Stack (`docker/hermes/`)

Hermes runs as 3 containers on the internal `hermes_net` network:

| Container | Image | Port | Purpose |
|---|---|---|---|
| `hermes_core` | `nousresearch/hermes-agent:latest` | 8642 (API), 9119 (Dashboard) | Core agent + web UI |
| `hermes_mobile_ui` | `ghcr.io/nesquena/hermes-webui:latest` | 8787 | Android-compatible web frontend |
| `hermes_relay` | built from source | 8767 | WSS bridge for Android app |

- **Data directory:** `/mnt/data/.agents/hermes` (bind-mounted to `/opt/data/hermes` in gateway)
- **Android app URL:** `http://<host-ip>:8767` (relay)
- **Dashboard URL:** `http://<host-ip>:9119`
- **API endpoint:** `http://hermes:8642` (internal Docker DNS)
- **Bare-metal fallback:** systemd services at `/home/maradmin/.config/systemd/user/hermes-gateway.service` (usually stopped)

### Configuration

- **Adding a Service:** Create a new directory. Reference the root `.env` using `env_file: ../.env`. Connect to the
  `proxy` network if web access is required.
- **Hardware Acceleration (GPU Governance):**
    - **HARD CONSTRAINT:** Do NOT modify existing GPU provisioning.
    - **Current Allocations:**
        - **Twin 3060 RTXs:** 100% reserved for LLM hosting (e.g., `vllm`, `sglang`, `llama-cpp`).
        - **3050 RTX:** Fully utilized by `immich` and `frigate`.
    - **Mandate:** NEVER enable GPU acceleration for new services or modify existing `device_ids` without explicit user
      approval.
    - **Implementation:** When authorized, use long UUIDs from `GPUS.md`. Set both `NVIDIA_VISIBLE_DEVICES` and
      `device_ids` for persistence.

### Database Access

Any time a user refers to a database or table, connect to the shared PostgreSQL instance in the `database/` stack
(container `db`, the primary datastore). It is the single source of truth for the domain databases (e.g. `myhealth_anthem`,
`myhealth_auth`, `myhealth_ucla`, `mathesar_django`) and all of their tables.

Use the following connection string **without** the username/password:

```
postgresql://db:5432/postgres
```

- **Host:** `db` (Docker DNS from the `proxy` network) — or `localhost` when reaching it from the host
- **Port:** `5432`
- **Database:** `postgres` (default; target the specific app database as needed)
- **User / Password:** do **not** hardcode these. Read them from `database/.env` (mirrored into the root `../.env`),
  e.g. user `postgres` with `POSTGRES_PASSWORD`. Other app DB roles/passwords (e.g. `PAPERLESS_DB_PASS`, `MATHESAR_DB_PASS`)
  are also defined there.

### Patient Identity Lookup

Always resolve the patient first, before querying any clinical/claims data. The patient may live in **either**
`myhealth_anthem` OR `myhealth_ucla` — these are separate databases and cannot be joined across each other, so check
both as needed. The lookup schema is identical in each:

- `patients` — one row per known patient: `patient_id`, `provider` (`anthem`/`ucla`), and `entity_ref`.
- `entity_names` — resolves an `entity_ref` to a human-readable `name`; patients are the rows where
  `entity_type = 'Patient'`.
- Join on `patients.entity_ref = entity_names.entity_ref` to get the patient's name from a `patient_id` (or vice versa).

**To resolve a patient by name → `patient_id`** (run in `myhealth_anthem`, then `myhealth_ucla` if not found):
```sql
SELECT p.patient_id, p.provider, e.name
FROM patients p
JOIN entity_names e ON e.entity_ref = p.entity_ref
WHERE e.entity_type = 'Patient'
  AND lower(e.name) LIKE '%<name>%';
```

**To resolve a `patient_id` → name** (for a question that only cites the ID):
```sql
SELECT e.name, p.provider
FROM patients p
JOIN entity_names e ON e.entity_ref = p.entity_ref
WHERE e.entity_type = 'Patient'
  AND p.patient_id = '<patient_id>';
```

Guidelines:
- If a question would benefit from knowing the patient's identity (e.g., to scope data, disambiguate, or summarize a
  specific person), **ask the user for the patient's name first** if it isn't already given, then perform the lookup.
- Since the patient may be in `myhealth_anthem` or `myhealth_ucla`, query both databases unless the context already
  pins the source. An `entity_ref` carries its source prefix (e.g. `anthem:Patient:...` / `ucla:Patient:...`).
- Once you have the `patient_id`, keep the query scoped to it (and the correct source database) — never run unfiltered
  queries across all patient rows.

Notes:
- The database stack also runs `db_redis` (Redis, port 6379), `marimo`, `zeppelin`, `postgres-mcp` (SSE endpoint port
  8089), `mathesar` (Web GUI port 8092), and `cloudbeaver` (Web GUI port 8978).
- Never expose or log credentials; keep passwords sourced from `.env`, never inline them in queries or output.

## Development Conventions

- **Autonomy:** Never create dependencies between stack directories unless absolutely necessary (e.g., via external
  networks).
- **Labels:** Strictly follow label patterns for:
- **Traefik:** For routing and SSL.
- **Homepage:** For dashboard visibility.
- **WUD:** For update notifications.
- **Volumes:** Use relative paths (e.g., `./config`) for persistent service configuration to keep stacks portable. Use
  absolute paths (e.g., `/mnt/storage/...`) only for bulk data stores.
- **Plans & Documentation:** Always write plans to `/mnt/data/scripts/docs/` and prefix the filenames with
  `yyyymmdd-hhmmss-` based on the current timestamp (e.g., `yyyymmdd-hhmmss-plan_name.md`).
- **Sudo Constraint:** NEVER default to using `sudo` for simple file or directory operations. Sudo should only be used
  when strictly required (e.g., modifying system config files or reloading sysctl parameters) and should never be used
  as a shortcut.

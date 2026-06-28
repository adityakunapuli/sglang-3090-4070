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

## Key Technologies

- **Orchestration:** Docker Compose (Independent per stack)
- **Ingress/Proxy:** Traefik (External network: `proxy`)
- **Dashboard:** Homepage (Unified view of all independent services)
- **Monitoring:** Telegraf/InfluxDB/Grafana (Service-agnostic monitoring)
- **AI Stack:** vLLM, sglang, LiteLLM (absent), Open WebUI, Llama-cpp, llama-swap, Gateway (custom), Bifrost
- **Media Stack:** Plex, Audiobookshelf, Sonarr, Radarr, Prowlarr
- **Updates:** WUD (What's Up Docker) monitoring the local socket for all stacks

---

## ML Service Topology

There is a **bifurcated** LLM routing architecture with multiple "gateways" and inconsistent consumer connectivity. The
goal is to consolidate into a single unified gateway. Below is the current catalog.

### ML Hosting Backends (Model Servers)

These are the actual inference engines that load models onto GPU and serve OpenAI-compatible APIs.

| Service        | Directory     | Port(s)              | GPU            | Models Served                                                                       | Role                                                                                                                                     |
|----------------|---------------|----------------------|----------------|-------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------|
| **llama-swap** | `llama-swap/` | 8082:8080, 5800:5800 | Both RTX 3060s | Gemma-4-12B-MTP, Gemma4-26B-A4B-QAT, Qwen3.6-27B, Gemma4-31B-QAT, Surya-OCR-2 (CPU) | Hot-swappable model manager; manages llama-server processes on-demand with TTL, matrix sets (e.g., Surya-OCR-2 + Gemma-26B concurrently) |
| **llama-cpp**  | `llama-cpp/`  | 8082:8080            | Both RTX 3060s | Same models as llama-swap                                                           | Standalone llama-server router mode (mutually exclusive with llama-swap since both use port 8082)                                        |
| **vLLM**       | `vllm/`       | 7777:7777            | Both RTX 3060s | Gemma-4-12B-QAT-AWQ, Gemma-4-26B-A4B-AWQ, Qwen3.6-27B (via `.cmd` profiles)         | High-throughput inference server; experimental, manual start (`restart: "no"`)                                                           |
| **SGLang**     | `sglang/`     | 30000:30000          | Both RTX 3060s | Gemma-4-12B-it-qat-w4a16-ct                                                         | EAGLE speculative decoding; experimental, manual start (`restart: "no"`)                                                                 |

**Note:** Port 8082 is the "bottleneck" -- both `llama-swap` and `llama-cpp` map it. They are mutually exclusive.

### Gateway / Proxy Layer

These services sit between consumers and backends, providing request transformation, API key management, model aliasing,
and telemetry.

| Service              | Port     | Backend Target                                                 | Features                                                                                                                                                       |
|----------------------|----------|----------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Gateway** (custom) | 4001     | `http://192.168.254.111:8082/v1/chat/completions`              | Per-model overrides (temp, max_tokens, thinking on/off), auto model resolution, `extra_body` hoisting for llama-server, per-request telemetry (TTFT, tokens/s) |
| **Bifrost**          | 8083     | `http://llama-cpp:8080/v1` (Docker DNS)                        | API key management layer; configured provider "llama-cpp-frigate" with keys for models `frigate` & `Gemma-4-12B-MTP`                                           |
| **LiteLLM**          | (absent) | Referenced at `litellm:4000` but **not deployed** in any stack | Referenced in `genai.env` and by Open WebUI, but no docker-compose exists                                                                                      |

### Consumer Services (Downstream LLM Clients)

These services send chat/completion requests to the hosting layer.

| Consumer                | Connects To (URL)                | Method                          | Model Alias           | Notes                                                                                                                                             |
|-------------------------|----------------------------------|---------------------------------|-----------------------|---------------------------------------------------------------------------------------------------------------------------------------------------|
| **Frigate**             | `http://gateway:4000/v1`         | Docker DNS via `proxy` network  | `frigate`             | Gateway resolves `frigate` alias to active model, disables thinking. Also has `genai.env` refs to `litellm:4000` but overrides to `gateway:4000`. |
| **Paperless-ngx (AI)**  | `http://192.168.254.111:8082/v1` | Direct IP (bypasses Docker DNS) | `ocr`                 | Paperless AI Next sidecar. Uses direct host IP to access port 8082 (llama-swap or llama-cpp).                                                     |
| **Paperless-ngx (OCR)** | `http://192.168.254.111:4001/v1` | Direct IP via Gateway           | `Surya-OCR-2`         | OCR path goes through Gateway (port 4001) for Surya model access.                                                                                 |
| **Hermes Agent**        | `http://192.168.254.111:8082/v1` | Direct IP (bypasses gateway)    | Default backend model | NousResearch Hermes agent; isolated on `agent_net` (not on `proxy`).                                                                              |
| **Open WebUI**          | `http://litellm:4000/v1`         | Docker DNS (but LiteLLM absent) | Generic               | Currently a dead link -- will fail unless LiteLLM is deployed externally.                                                                         |
| **Copaw**               | (host network)                   | Host network mode               | Unknown               | QwenPaw agent with full host privileges; no explicit LLM URL configured.                                                                          |

### Network Topology Diagram

```
                          PROXY NETWORK (external)
     ==================================================================
     |        |         |          |         |         |         |     |
  [Gateway] [llama-] [llama-]   [vLLM]   [SGLang] [Bifrost] [Frigate]
   (4001)   [swap]   [cpp]     (7777)    (30000)   (8083)    (5000)
             (8082)  (8082)
     |        |         |                                           |
     |        +----+----+                                           |
     |             |                                                |
     |      [Hermes Agent]      [Open WebUI]              [genai.env]
     |      (direct IP:8082)    (litellm:4000             (frigate model,
     |                          but LiteLLM MIA)           url=gateway:4000)
     |
     |  [Paperless-ngx AI]
     |  (direct IP:8082/v1) -- bypasses gateway for AI
     |
     |  [Paperless-ngx OCR]
     |  (direct IP:4001/v1) -- uses gateway for OCR
     |
     +----[ LAN Host 192.168.254.111 ]----(for direct port access)

     [QwenPaw] - host network (no container networking)
     [Immich] - proxy network (no LLM dependency, uses own ML pipeline)
```

### Current Bifurcation Problem

There are **three distinct "gateway" concepts** and **two consumer connectivity patterns**, leading to fragmentation:

1. **Custom Gateway** (`gateway/`, port 4001) -- Owned and runs. Used by Frigate and Paperless-OCR.
2. **Bifrost** (`bifrost/`, port 8083) -- Owned and runs. Provides API key management but **no consumers actually use
   it** (Frigate bypasses to Gateway directly).
3. **LiteLLM** (absent) -- Referenced by `genai.env` and Open WebUI, but never deployed.

**Connectivity inconsistency:**

- **Docker DNS consumers:** Frigate → `gateway:4000`, Open WebUI → `litellm:4000` (dead)
- **Direct IP consumers:** Paperless → `192.168.254.111:8082`, Hermes → `192.168.254.111:8082`, Paperless-OCR →
  `192.168.254.111:4001`
- **Host network:** Copaw (unknown routing)

**Desired state:** A single unified gateway (likely replacing/extending the custom Gateway or adding LiteLLM) that all
consumers address consistently, with:

- Unified API key management
- Consistent model aliasing & per-consumer overrides
- Telemetry and observability across all requests
- Elimination of direct-backend connections

## Operational Procedures

### Managing Services

Since there is no central `docker-compose.yml`, all commands must be run within the specific service directory:

- **Start/Update Service:** `cd <service-dir> && docker compose up -d`
- **Stop Service:** `cd <service-dir> && docker compose down`
- **Check Logs:** `cd <service-dir> && docker compose logs -f`

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

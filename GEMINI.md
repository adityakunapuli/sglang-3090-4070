# Project Overview
This workspace is a **Service-First (Modular) Home Server Infrastructure**. 

Unlike monolithic setups, this project is designed around **service autonomy**. Every directory is an independent, self-contained Docker Compose stack. This architecture ensures that a failure or configuration change in one service (e.g., an LLM engine) does not impact the stability of others (e.g., DNS or Media).

## Architecture & Structure
The infrastructure is decentralized. There is **no root orchestrator**.

- **Modular Stacks:** Services are grouped into logical directories (e.g., `arr-stack`, `immich`, `monitoring`, `openwebui`). 
- **Scratch Space:** The `.archive/` directory is a git-ignored scratch space reserved for creating test scripts, sharing screenshots, and transient experimentation.
- **Global Ingress:** A shared `proxy` network (managed by Traefik, which resides in its own isolated directory) acts as the unified gateway for all web-facing services.
- **Shared Secrets:** A centralized `.env` file in the root directory provides consistent API keys, paths, and hardware UUIDs across multiple stacks via `env_file: ../.env`.
- **Inter-Service Communication:** Services connect to the `proxy` network for external access and use internal `default` networks for stack-specific communication.

## Key Technologies
- **Orchestration:** Docker Compose (Independent per stack)
- **Ingress/Proxy:** Traefik (External network: `proxy`)
- **Dashboard:** Homepage (Unified view of all independent services)
- **Monitoring:** Telegraf/InfluxDB/Grafana (Service-agnostic monitoring)
- **AI Stack:** vLLM, sglang, LiteLLM, Open WebUI, Llama-cpp
- **Media Stack:** Plex, Audiobookshelf, Sonarr, Radarr, Prowlarr
- **Updates:** WUD (What's Up Docker) monitoring the local socket for all stacks

## Operational Procedures

### Managing Services
Since there is no central `docker-compose.yml`, all commands must be run within the specific service directory:
- **Start/Update Service:** `cd <service-dir> && docker compose up -d`
- **Stop Service:** `cd <service-dir> && docker compose down`
- **Check Logs:** `cd <service-dir> && docker compose logs -f`

### Configuration
- **Adding a Service:** Create a new directory. Reference the root `.env` using `env_file: ../.env`. Connect to the `proxy` network if web access is required.
- **Hardware Acceleration (GPU Governance):**
    - **HARD CONSTRAINT:** Do NOT modify existing GPU provisioning. 
    - **Current Allocations:**
        - **Twin 3060 RTXs:** 100% reserved for LLM hosting (e.g., `vllm`, `sglang`, `llama-cpp`).
        - **3050 RTX:** Fully utilized by `immich` and `frigate`.
    - **Mandate:** NEVER enable GPU acceleration for new services or modify existing `device_ids` without explicit user approval.
    - **Implementation:** When authorized, use long UUIDs from `GPUS.md`. Set both `NVIDIA_VISIBLE_DEVICES` and `device_ids` for persistence.

## Development Conventions
- **Autonomy:** Never create dependencies between stack directories unless absolutely necessary (e.g., via external networks).
- **Labels:** Strictly follow label patterns for:
    - **Traefik:** For routing and SSL.
    - **Homepage:** For dashboard visibility.
    - **WUD:** For update notifications.
- **Volumes:** Use relative paths (e.g., `./config`) for persistent service configuration to keep stacks portable. Use absolute paths (e.g., `/mnt/storage/...`) only for bulk data stores.

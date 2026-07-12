# Paperless-ngx Stack — Agent Reference

**Headless Ubuntu Server** running on `192.168.254.111` — all interaction via SSH/Docker.

Paperless-ngx is a document management system that ingests files from a consume directory, OCRs them, and provides a searchable document archive. This stack includes a custom OCR sidecar that replaces the built-in Tesseract OCR with LLM-based vision OCR and AI metadata extraction.

## Architecture Overview

```
paperless-ngx/
├── docker-compose.yml          # 6 services: webserver, db, broker, gotenberg, tika, ocr-sidecar
├── .env                        # Shared config (DB creds, API tokens, AI settings, gateway URL)
├── ai-system-prompt.txt        # Legacy system prompt for document analysis (not currently used by sidecar)
│
├── data/                       # Paperless-ngx application data (SQLite index, logs, classifier model)
│   ├── db.sqlite3              # Paperless metadata DB (documents, tags, correspondents, etc.)
│   ├── log/                    # paperless.log + celery.log (rotated)
│   ├── index/                  # Whoosh full-text search index
│   └── classification_model.pickle  # ML classifier for auto-matching
│
├── db/                         # PostgreSQL data (paperless primary DB)
├── redis/                      # Redis data (Celery broker)
│
├── ocr-sidecar/                # CUSTOM: LLM-based OCR + AI metadata extraction service
│   ├── app/
│   │   ├── main.py             # FastAPI app: scheduler, scan loop, API endpoints, document processing
│   │   ├── config.py           # Environment-based config (OCR API, AI API, prompts, scan interval)
│   │   ├── ocr.py              # OcrProcessor: PDF→page images→LLM vision API→text
│   │   ├── ai.py               # AiAnalyzer: LLM text analysis → JSON metadata (title, tags, etc.)
│   │   ├── paperless.py        # PaperlessClient: REST API client (download, update, tag, metadata)
│   │   └── database.py         # SQLite sidecar DB (tracks processed docs, scan logs)
│   ├── prompts/
│   │   └── analysis.md         # AI analysis prompt (JSON extraction with tag rules)
│   ├── static/                 # Simple web UI for monitoring
│   ├── Dockerfile              # Python 3.12-slim + httpx, PyMuPDF, apscheduler, FastAPI
│   └── requirements.txt
│
├── surya-ocr/                  # UNUSED: Surya-OCR sidecar (built but not in docker-compose)
│   ├── app.py                  # FastAPI shim that exposes Surya OCR as OpenAI-compatible chat API
│   └── Dockerfile
│
├── paperless-ai-data/          # DEFUNCT: Old PaperlessGPT/AI sidecar data (no running service)
├── paperless-ai-next-data/     # DEFUNCT: Paperless-AI-Next sidecar data (no running service)
└── gpt-data/                   # DEFUNCT: Legacy GPT integration data
```

## Services

| Service           | Container              | Image                                    | Port     | Role                                                                         |
|-------------------|------------------------|------------------------------------------|----------|------------------------------------------------------------------------------|
| **webserver**     | paperless_web          | ghcr.io/paperless-ngx/paperless-ngx:latest | 8010:8000 | Main Paperless-ngx app (web UI, API, inotify consume watcher, Celery worker) |
| **db**            | paperless_db           | postgres:18                              | 5432     | PostgreSQL primary database                                                  |
| **broker**        | paperless_redis        | redis:8                                  | 6379     | Celery message broker (task queue)                                            |
| **gotenberg**     | paperless_gotenberg    | gotenberg/gotenberg:7.10                 | 3000     | PDF rendering (Office docs → PDF for OCR)                                     |
| **tika**          | paperless_tika         | apache/tika:latest                       | 9998     | Apache Tika (metadata extraction from various file types)                     |
| **ocr-sidecar**   | paperless_ocr_sidecar  | (built from ./ocr-sidecar)               | 8002:8002 | Custom LLM-based OCR + AI metadata extraction (replaces built-in Tesseract) |

## Volume Mappings

| Host Path                           | Container Path                  | Purpose                                    |
|-------------------------------------|---------------------------------|--------------------------------------------|
| `./data`                            | `/usr/src/paperless/data`       | App data (logs, index, classifier, SQLite) |
| `/mnt/storage/documents/archive`    | `/usr/src/paperless/media`       | Document archive (consumed files stored here) |
| `/mnt/storage/documents/export`     | `/usr/src/paperless/export`      | Export directory                            |
| `/mnt/storage/documents/consume`     | `/usr/src/paperless/consume`     | Consume folder (input — paperless watches this) |
| `./db`                              | `/var/lib/postgresql`            | PostgreSQL data                            |
| `./redis`                           | `/data`                         | Redis data                                 |

## OCR Sidecar Design

The sidecar is a FastAPI app that runs on a cron schedule (`SCAN_INTERVAL`). It:

1. **Fetches** all Paperless documents that DON'T have the `ocr-processed` tag
2. For each PDF document:
   - Downloads the PDF from Paperless API
   - Renders each page to a PNG image (PyMuPDF, 200 DPI, max 50 pages)
   - Sends each page image to the LLM gateway as a vision request (`OCR_API_URL`)
   - Receives transcribed text back
   - Writes the OCR text back to Paperless via API
3. After OCR, performs **AI analysis** (supposed to, but currently has a bug — see Known Issues):
   - Sends the OCR text to the LLM with the analysis prompt
   - Receives JSON with title, correspondent, tags, document_type, custom fields
   - Applies metadata to the Paperless document via API
4. Adds the `ocr-processed` tag to mark the document as processed

### Sidecar Configuration (environment in docker-compose.yml)

| Env Var                | Value                                              | Purpose                                    |
|------------------------|----------------------------------------------------|--------------------------------------------|
| `PAPERLESS_URL`        | `http://192.168.254.111:8010`                      | Paperless API base URL                     |
| `PAPERLESS_API_TOKEN`  | `fb3e...`                                          | Paperless REST API token                   |
| `OCR_API_URL`          | `http://gateway:4000/v1/chat/completions`           | LLM gateway endpoint for OCR (vision)       |
| `OCR_API_KEY`          | `paperless-ai-ocr`                                 | Gateway auth token                          |
| `OCR_MODEL`            | `ocr`                                              | Gateway model alias (resolves to running model) |
| `SCAN_INTERVAL`        | `0 * * * *`                                        | Hourly cron scan                            |
| `PROCESSED_TAG`        | `ocr-processed`                                    | Tag added after processing (also used as skip tag) |
| `SKIP_TAG`             | `ocr-processed`                                    | Tag used to skip already-processed docs     |
| `AI_ANALYSIS_ENABLED`  | `yes`                                              | Enable AI metadata extraction               |
| `AI_MODEL`             | `ocr`                                              | Gateway model alias for AI analysis         |
| `AI_API_URL`           | `http://gateway:4000/v1/chat/completions`           | LLM gateway endpoint for analysis           |
| `AI_API_KEY`           | `paperless-ai-ocr`                                 | Gateway auth token                          |
| `MAX_PAGES`            | `50`                                               | Max pages to OCR per document               |
| `MAX_TOKENS_PER_PAGE`  | `8192`                                             | Max tokens per OCR page                     |
| `MAX_ANALYSIS_TOKENS`  | `4096`                                             | Max tokens for AI analysis response         |

### Gateway Model Resolution

The sidecar uses model alias `ocr` which the gateway resolves to `model: "auto"` — meaning it queries the upstream (llama-swap on port 8082) for whatever model is currently loaded. This means the actual model used depends on what llama-swap has active at the time of the request.

For OCR to work, the active model in llama-swap must support **vision/image input** (e.g., GLM-OCR, Gemma-4-12B-MTP). If a text-only model (e.g., Qwen3.6-27B) is active, OCR requests will fail with 502 errors.

### Sidecar API Endpoints

| Method | Path                              | Purpose                                    |
|--------|-----------------------------------|--------------------------------------------|
| GET    | `/`                               | Web UI (static dashboard)                   |
| GET    | `/api/config`                     | Current config                              |
| GET    | `/api/stats`                      | Processing statistics                        |
| GET    | `/api/documents`                   | List all documents with sidecar status       |
| GET    | `/api/documents/{doc_id}`          | Document detail with OCR text preview        |
| POST   | `/api/documents/{doc_id}/reprocess`| Reprocess a single document                  |
| POST   | `/api/documents/reprocess-batch`   | Reprocess multiple documents                 |
| POST   | `/api/reprocess-all`               | Trigger a full scan cycle                    |
| GET    | `/api/logs`                        | Recent scan logs                            |
| GET    | `/api/playground/models`           | Available LLM models                         |
| POST   | `/api/playground/prompt`            | Test custom prompts against documents        |
| GET    | `/health`                         | Health check                                |

## LLM Connectivity

```
[OCR Sidecar] --(Docker DNS: gateway:4000)--> [Gateway (4001)]
                                                    |
                                                    v
                                        [llama-swap (8082)] --> [GPU models]
```

- OCR sidecar is on both `default` and `proxy` networks
- Gateway is on the `proxy` network (accessible via Docker DNS `gateway:4000`)
- Gateway upstream: `http://192.168.254.111:8082/v1/chat/completions` (host IP for llama-swap)
- Gateway "ocr" alias: `model: "auto"` (resolves to whatever llama-swap has running), thinking disabled, temperature 0.0

## Defunct AI Services

Two previous AI sidecar services existed but are no longer running (no service in docker-compose.yml):

1. **paperless-ai-data/** — Old PaperlessGPT-style sidecar. Configured with `SCAN_INTERVAL="* * * * *"`, system prompt with tag rules, `CUSTOM_MODEL=OCR`. Had its own API token (`82880c...`).

2. **paperless-ai-next-data/** — Paperless-AI-Next (Node.js). Logs show it was set up around Jun 20, 2026 but failed during configuration: "Private IP addresses are not allowed" errors when trying to validate the gateway URL, followed by repeated `PAPERLESS_API_URL` null reference errors every minute. It was never successfully configured and is not running.

These services previously handled document tagging (title, correspondent, tags, document_type). Documents processed before they were removed have rich metadata; documents processed after only have the `ocr-processed` tag.

## Deployment Commands

```bash
# Start / update the stack
cd /mnt/data/docker/paperless-ngx && docker compose up -d

# Stop
cd /mnt/data/docker/paperless-ngx && docker compose down

# Rebuild OCR sidecar (after code changes)
cd /mnt/data/docker/paperless-ngx && docker compose build ocr-sidecar && docker compose up -d ocr-sidecar

# Logs
docker compose logs -f webserver    # Paperless app + consume watcher
docker compose logs -f ocr-sidecar  # OCR + AI processing
docker compose logs -f celery       # Background tasks

# Paperless logs (file-based)
tail -f data/log/paperless.log      # Consumption + OCR (OCRmyPDF/Tesseract)
tail -f data/log/celery.log         # Celery beat + task execution
```

## Key Files

- **Consume directory:** `/mnt/storage/documents/consume/` — Drop files here for ingestion. Paperless watches this with inotify (recursive with `PAPERLESS_CONSUMER_RECURSIVE=true`).
- **Archive directory:** `/mnt/storage/documents/archive/` — Consumed documents are stored here in `documents/originals/` and `documents/thumbnails/`.
- **Paperless API:** `http://192.168.254.111:8010/api/` — REST API (token auth).
- **Sidecar UI:** `http://192.168.254.111:8002/` — Monitor OCR processing status.
- **Sidecar DB:** `./ocr-sidecar/sidecar-data/sidecar.db` — SQLite tracking DB.

## Conventions

- **`restart: "no"`** on all services — intentional for debugging; containers do not auto-restart on failure.
- **`PAPERLESS_FILENAME_FORMAT={{ original_name }}`** — preserves original filenames in archive.
- **`PROCESSED_TAG = SKIP_TAG = "ocr-processed"`** — the sidecar uses the same tag for both marking processed and skipping. Once a document gets this tag, it's never re-examined by the sidecar.
- **OCR prompt** is hardcoded in `config.py` with rules for collapsing spacing, handling poor scans, and preserving structure.
- **AI analysis prompt** is loaded from `prompts/analysis.md` at container startup.
- **Custom fields** `AI_Summary` and `AI_KeyDetails` are written to Paperless if those custom fields exist.

## Known Issues

### 1. AI Metadata Extraction Never Executes
In `main.py:process_document()`, the `ai` parameter (AiAnalyzer instance) is received but **never called**. The `_apply_ai_metadata()` function exists (line 100) but is never invoked from anywhere in the codebase. This means:
- OCR text IS extracted and written back to Paperless
- But title, correspondent, tags, document_type, and custom fields are NEVER set by the sidecar
- Documents only get the `ocr-processed` tag — no descriptive tags

### 2. OCR Failures Treated as Success
In `ocr.py:ocr_document()`, when `ocr_page()` raises an exception (after 3 retries), the error text `"\n[OCR failed for page {i+1}]\n"` is appended to the page texts list. The combined string is non-empty, so `process_document()` treats it as valid OCR text and proceeds to write it to Paperless. The document gets the `ocr-processed` tag despite having no real OCR content.

### 3. OCR Model Must Support Vision
The gateway "ocr" alias uses `model: "auto"` which resolves to whatever model is currently loaded in llama-swap. If a text-only model (e.g., Qwen3.6-27B) is active, all OCR requests fail with 502 because the model can't process image input. A vision-capable model (e.g., GLM-OCR) must be active.

### 4. Consume Directory Subdirectory Files Not Picked Up
Paperless-ngx uses inotify to watch the consume directory. While `PAPERLESS_CONSUMER_RECURSIVE=true` is set, the inotify event-based watcher may not detect files in deeply nested subdirectories that were added while the watcher was already running. Files at the root of the consume directory are detected reliably.

### 5. Defunct AI Services Leave Orphaned Config
The `.env` file contains settings for "Paperless AInext" (lines 36-57) that reference a service not in docker-compose.yml. The `paperless-ai-data/.env` and `paperless-ai-next-data/.env` files contain stale configurations with different API tokens.

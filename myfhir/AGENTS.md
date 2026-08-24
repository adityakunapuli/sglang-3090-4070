# AGENTS.md — AI Agent Context

## Project Summary
Multi-provider FHIR data-pull job with a web dashboard. A scheduled backend
repeatedly fetches insurance data (Anthem/Elevance Health: EOBs, claims,
coverage) and clinical data (Epic — UCLA is the only configured Epic provider)
into a shared PostgreSQL instance, writing last-run summaries to a
`job_run` table. A Vite + React dashboard shows run status, record totals,
token health, and drives a manual-paste OAuth re-authentication flow.

Runs entirely in containers against the shared `../database` PostgreSQL
instance (network `proxy`, service `db`). Backend is internal-only; only the
frontend port is published.

## User Intent
- Keep a permanent, unattended data-pull pipeline (scheduled `myhealth job`)
  writing to Postgres with structured schemas and denormalized views.
- Dashboard to monitor last-run status, record totals/deltas, and quickly
  re-authenticate a provider whose refresh token expired (manual code
  copy/paste; no localhost listener; `REDIRECT_URI` never needs hosting).
- Run entirely locally. No PHI committed to the repo.

## Architecture

```
User Browser ──► Provider OAuth Login ──► Redirect to REDIRECT_URI?code=XXX
                                                    │ (user copies code into dashboard)
Dashboard (Vite + React, nginx SPA)
  │  /api/* proxied to backend (internal network only)
Backend (FastAPI + CLI)
  ├── config/settings.py        # provider registry: anthem (kind=anthem) + epic
  ├── api/dashboard.py          # GET /api/status, GET /api/auth/{provider}/start,
  │   │                         # POST /api/auth/{provider}/exchange
  ├── main.py                   # FastAPI app, lifespan starts job daemon thread
  ├── job/                      # run_provider / run_all / run_job_once / job_loop /
  │   │                         # start_job_thread; writes job_run summaries
  ├── services/auth.py          # AuthManager (PKCE, multi-patient tokens) + RefreshDaemon
  ├── services/fhir_client.py   # FHIRClient (Anthem EOB/claims, Epic clinical)
  ├── models/oauth.py           # OAuthToken + TokenStore (Postgres)
  └── db/                       # 3 sessions + models (auth/anthem/ucla)
DB (shared PostgreSQL)          # myhealth_auth / myhealth_anthem / myhealth_ucla
  └── now also: pkce_verifiers (PKCE verifier/state by provider),
                job_run (per-provider run summaries)
```

## Provider Configuration
- `config/settings.py` defines the registry via `resolve_provider(name)`.
- **anthem**: env `CLIENT_ID`, `CLIENT_SECRET`, `FHIR_PLAN` (default
  `AnthemBlueCross`), `build_fhir_base_url(plan)`.
- **epic** (UCLA): env `<NAME>_CLIENT_ID` / `<NAME>_SECRET`, falling back to
  shared `EPIC_CLIENT_ID` / `UCLA_SECRET` / `UCLA_SECRET_PROD` / `EPIC_SECRET`.
- `redirect_uri` = env `REDIRECT_URI` else default `https://localhost/callback`.
- `list_providers()` returns configured provider names; `list_epic_providers()`
  returns only the epic ones. The dashboard iterates `list_providers()`.
- To configure a new Epic site, add `<NAME>_CLIENT_ID` + `<NAME>_SECRET` env
  vars and register it in `config/settings.py` (`PROVIDERS` map) following the
  UCLA entry.

## Database
Three PG databases with explicit sessions (`db/__init__.py`):
- `myhealth_auth` — `oauth_tokens` (patient_id, provider) PK; `entity_names`;
  `patients`; `pkce_verifiers` (provider PK, verifier, state); `job_run`
  (running|success|partial|failed, totals counts JSON).
- `myhealth_anthem` — `eob`, `eob_item`, `eob_diagnosis`, `eob_care_team`,
  `eob_total`, `claim_submission`, `claim_item`, `claim_diagnosis`,
  `claim_care_team`, `member_claim`, `entity_names`, `member_claim_submission`
  (+ `submission_origin`/`is_out_of_network` classification columns + views).
- `myhealth_ucla` — `encounter`, `encounter_participant`, `diagnostic_report`,
  `lab_result`, `imaging_observation`, `clinical_observation`, `clinical_note`,
  `condition`, `procedure_record`, `medication_statement`, `medication_request`,
  `allergy_intolerance`, `immunization`, `care_plan`, `document_reference`,
  `family_member_history`, `medication_administration`, `service_request`,
  `specimen`, `communication`, `care_team`.

Provenance columns: `encounter`, `clinical_observation`, `clinical_note`,
`condition`, `medication_request`, `immunization`, `medication_administration`,
`service_request`, `specimen`, `communication`, `care_team` carry `source`
(`fhir` | `ehi`) + `source_id`. EHI (Epic EHI export) rows use namespaced
synthetic `fhir_id` values: `ehi_note_<NOTE_ID>`, `ehi_mar_<ORDER_MED_ID>_<LINE>`,
`ehi_enc_<CSN>`, `ehi_vitals_<FSD_ID>_<LINE>`,
`ehi_medorder_<ORDER_MED_ID>`, `ehi_procorder_<ORDER_PROC_ID>`,
`ehi_imm_<DOCUMENT_ID>`.

Views: Anthem `eob_claims`, `eob_items`, `claim_submissions`, `claim_items`,
`member_claims_recon`, `member_claims_summary`, `member_claims`; UCLA
`lab_results`, `clinical_overview`.

`db.init_db()` runs `create_all` on each base — tables are idempotent. The
`schema.md` in `docs/` is the schema authority; read it before changing ORM
models, migrations, views, or persistence code.

## Key Code Patterns
- Provider resolution: `resolve_provider('anthem'|'ucla')` → `ProviderConfig`
  (`name`, `display_name`, `kind`, `redirect_uri`, `fhir_base_url`, `auth_url`,
  `token_url`, `auth_command()`).
- Auth: `get_auth_manager(provider)` → cached `AuthManager`;
  `build_authorize_url(state=...)` persists the PKCE verifier to the DB;
  `exchange_code(code)` loads+clears it; `status()` → per-patient token health;
  `get_valid_token(patient_id=...)`; `RefreshDaemon(provider, interval, threshold)`.
- FHIR: `get_fhir_client(provider)` → cached `FHIRClient`; `search()` /
  `search_all()`; `_with_retry()` catches 401 → forced refresh → retry once.
- Job: `job.run_job_once(providers=None, skip_labs=False)` — one full sweep;
  `job.run_provider(provider, skip_labs)` — single provider, records `job_run`;
  `job.job_loop(interval)` + `start_job_thread(interval_minutes)` for the daemon.
- DB session helpers: `get_auth_session()` / `get_anthem_session()` /
  `get_ucla_session()`; `get_session_for(provider)` + `get_clinical_session(provider)`
  via the `PROVIDER_DB` map.
- CLI: `myhealth job [--daemon] [--interval-minutes N] [-p provider] [--skip-labs]`;
  `myhealth anthem auth login|status`; `myhealth ucla save-labs`; `myhealth anthem eob`.
- EHI import: `myhealth ucla ehi <export-dir> -p <FHIR_PATIENT_ID> [--no-db]
  [--only notes|mar|encounters|vitals|orders|immunizations]` →
  `EHIImporter` in `services/ehi_importer.py` (wrapper `import_ehi_export`).
  Reads an Epic EHI export dir (`EHITables/`, `Rich Text/`, `Media/`) and
  merges into the `myhealth_ucla` tables as `source='ehi'`. The export is PHI —
  pass the path at runtime only, never commit it. Encounters import first
  (backfills the `source_id` CSN→encounter map); RTF note bodies are parsed to
  text (`Rich Text/HNO_<NOTE_ID>_<CSN>_41.rtf`, `HNO_PLAIN_TEXT.tsv` fallback);
  deleted notes (`DELETE_INSTANT_DTTM`) are skipped. Media binaries and the
  CLARITY_EDG dictionary are excluded (no BLOB storage).
- `myhealth ucla save-all --wipe` truncates the UCLA clinical tables
  (independent of `myhealth_auth` / tokens) then refetches from FHIR.
- DB URL resolution: `db._resolve_db_url()` uses `MYHEALTH_DB_*` from the
  project `.env` (or `DATABASE_URL` if explicitly exported). When running
  outside Docker, host `db` maps to `127.0.0.1`.
- Foreign-key safety: missing FHIR references stored as `NULL`; never fabricate
  placeholder text/values in FK or identity columns.
- CLI shape safety: FHIR fields may be object or array; normalize cardinality
  before iterating.

## Running
```bash
uv sync --project backend

# containers (uses shared ../database DB + proxy network)
docker compose --env-file ../.env up -d --build
# dashboard → http://<host>:8517

# host (non-container) dev
uv run --project backend myhealth job
uv run --project backend myhealth job --daemon --interval-minutes 1440
uv run --project backend myhealth anthem auth status
uv run --project backend myhealth anthem eob --no-db
uv run --project backend myhealth ucla save-labs --no-db
# EHI export import (PHI — runtime path only, never committed)
uv run --project backend myhealth ucla ehi /path/to/export -p <FHIR_PATIENT_ID>
uv run --project backend myhealth ucla ehi /path/to/export -p <ID> --only notes --no-db
```

## OAuth2 Flow (manual paste)
1. `GET /api/auth/{provider}/start` → builds authorize URL (PKCE verifier +
   state saved to `pkce_verifiers`), returns URL.
2. User logs in on provider; browser redirects to `REDIRECT_URI?code=...`.
3. `POST /api/auth/{provider}/exchange` with `{"redirect_url": "..."}` (the
   pasted address bar). Code is extracted from URL query (or raw code),
   exchanged for tokens, stored keyed by `(patient_id, provider)`.

## Security Constraints
- Never log tokens, secrets, or patient data to stdout beyond formatted summaries.
- `.env`, `.archive/`, `*.db`, `node_modules/` are gitignored.
- Dashboard `POST /api/auth/{provider}/exchange` returns no secrets; keep the
  backend unpublished (internal network only).
- PHI must never be committed. Keep the tests anonymized; add real patient
  identifiers only to local (gitignored) `.env`.

## Tests
```bash
uv run --project backend pytest backend/tests -q
```
Note: three `test_member_submissions` cases fail in both the private source
repo and here (pre-existing; unrelated to this build). Fix in the source first
if desired.

## Known Provider Limitations (Epic/UCLA)
- ImagingStudy → 403 (missing scope). DiagnosticReport (imaging) is minimal.
- Epic rejects searches with multiple category params → use `-c all` to drop
  the filter.
- DICOM/PACS served via patient portal only (WADO-RS/DICOMweb 404).
- Flowsheet vitals in the EHI export have no numeric value column — the EHI
  importer records measurement name + recorded time only.
- `Media/` in an EHI export (PDFs, images, audio) is intentionally not
  imported (no BLOB storage); CLARITY_EDG (drug dictionary) is also excluded.
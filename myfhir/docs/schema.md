# MyFHIR — Complete Database & API Reference

> One-shot reference for agents. Covers all three PostgreSQL databases, every table/column/constraint, the denormalized views with their SQL definitions, and the API/CLI/Job layers that operate on them.

---

## Table of Contents

1. [Connection Details](#connection-details)
2. [Architecture Overview](#architecture-overview)
3. [FHIR-to-Table Mapping](#fhir-to-table-mapping)
4. [Database: myhealth_auth](#database-myhealth_auth)
5. [Database: myhealth_anthem](#database-myhealth_anthem)
6. [Database: myhealth_ucla](#database-myhealth_ucla)
7. [Denormalized Views](#denormalized-views)
8. [API Endpoints](#api-endpoints)
9. [CLI Commands](#cli-commands)
10. [Job System](#job-system)
11. [ORM Model Files](#orm-model-files)
12. [EHI Import System](#ehi-import-system)
13. [Cross-Database Relationships](#cross-database-relationships)

---

## Connection Details

```
Host:     db (internal Docker) / 127.0.0.1 (host)
Port:     5432
User:     postgres
Password: (see ../database/.env → POSTGRES_PASSWORD)
```

### Databases

| Database | Tables | Owner | Purpose |
|----------|--------|-------|---------|
| `myhealth_auth` | 17 | `myhealth` | OAuth tokens, PKCE, patients, job runs |
| `myhealth_anthem` | 16 + 7 views | `myhealth` | Insurance: EOBs, claims, member submissions |
| `myhealth_ucla` | 24 + 2 views | `myhealth` | Clinical: encounters, labs, conditions, etc. |

### Connecting from host

```bash
# Auth DB
PGPASSWORD="$(grep POSTGRES_PASSWORD ../database/.env | cut -d= -f2)" \
  psql -h 127.0.0.1 -p 5432 -U postgres -d myhealth_auth

# Anthem DB
PGPASSWORD="$(grep POSTGRES_PASSWORD ../database/.env | cut -d= -f2)" \
  psql -h 127.0.0.1 -p 5432 -U postgres -d myhealth_anthem

# UCLA DB
PGPASSWORD="$(grep POSTGRES_PASSWORD ../database/.env | cut -d= -f2)" \
  psql -h 127.0.0.1 -p 5432 -U postgres -d myhealth_ucla
```

### Session helpers in code

```python
from db import get_auth_session, get_anthem_session, get_ucla_session
from db import get_session_for, get_clinical_session

# Provider-aware (e.g. get_session_for('ucla') → get_ucla_session())
```

DB URL resolution (`db._resolve_db_url()`):
- Uses `MYHEALTH_DB_*` env vars from project `.env`
- Falls back to `DATABASE_URL` if explicitly exported
- When running outside Docker, host `db` resolves to `127.0.0.1`

---

## Architecture Overview

The system stores health data from two providers in separate databases, with OAuth tokens in a shared third database:

```
User Browser ──► Provider OAuth Login ──► Redirect to REDIRECT_URI?code=XXX
                                                    │ (user copies code into dashboard)
Dashboard (Vite + React, nginx SPA)
  │  /api/* proxied to backend (internal network only)
Backend (FastAPI + CLI)
  ├── config/settings.py        # provider registry: anthem (kind=anthem) + epic/ucla
  ├── api/dashboard.py          # GET /api/status, GET /api/auth/{provider}/start,
  │   │                         # POST /api/auth/{provider}/exchange
  ├── main.py                   # FastAPI app, lifespan starts job daemon thread
  ├── job/                      # run_provider / run_all / run_job_once / job_loop
  ├── services/auth.py          # AuthManager (PKCE, multi-patient tokens) + RefreshDaemon
  ├── services/fhir_client.py   # FHIRClient (Anthem EOB/claims, Epic clinical)
  ├── models/oauth.py           # OAuthToken + TokenStore (Postgres)
  └── db/                       # 3 sessions + models (auth/anthem/ucla)
```

### Token Replication

```
myhealth_auth (PRIMARY)
  ├── oauth_tokens  ← written here first during /api/auth exchange
  ├── patients
  ├── entity_names
  └── pkce_verifiers
            │
          synced at startup
            │
    ├── myhealth_anthem (REPLICA)         myhealth_ucla (REPLICA)
    ├── oauth_tokens (copy)               ├── oauth_tokens (copy)
    ├── patients (copy)                   ├── patients (copy)
    └── entity_names (copy)               └── entity_names (copy)
```

`oauth_tokens`, `patients`, and `entity_names` are replicated from `myhealth_auth` into both provider DBs via `_sync_oauth_tokens_replica()`. This allows denormalized views to join patient names locally without cross-DB queries.

### Identity Resolution

All entity references use the pattern `{provider}:{FHIRType}:{id}`, e.g. `anthem:Practitioner:abc123`.

- Base tables store only `*_ref` columns with canonical references
- `entity_names` resolves references to human-readable names and NPIs
- Views denormalize references back to names via LEFT JOINs
- `provider` in `entity_ref` namespace prevents Anthem/UCLA identity collision

### Patient ID Flow

`patient_id` is extracted from the `sub` claim of the JWT `id_token` during OAuth2. Every data row carries `patient_id` for filtering, but does not FK to the tokens table (tokens are in a different DB).

---

## FHIR-to-Table Mapping

### Anthem FHIR Resources (Insurance)

#### `ExplanationOfBenefit` → 5 tables

```
ExplanationOfBenefit (one resource)
├── eob                          ← header: claim number, status, dates, payment, patient/provider refs
├── eob_item × N                 ← line items: HCPCS code, dates, amounts
│   └── eob_item_adjudication × M ← per-item adjudication details
├── eob_diagnosis × N            ← ICD diagnosis codes
├── eob_care_team × N            ← providers involved
└── eob_total × N                ← financial totals by category
```

**Parser:** `db/parser.py` — `load_eob()`, `load_item()`, `load_diagnoses()`, `load_care_team()`, `load_totals()`, `load_item_adjudications()`.

`eob_item` flattens common adjudication categories (submitted, allowed, paid_provider, paid_patient, deductible, coinsurance, copay, noncovered, discount, member_liability, allowed_units, adjustment_reason, payment_status) directly onto the row. Full adjudication array preserved in `eob_item_adjudication`.

**Populated by:** `FHIRClient.save_eobs_to_db()` via `get_anthem_session()`. Upserted by `eob.id`; old children deleted and re-inserted on each upsert.

#### `Claim` → 4 tables

```
Claim (one resource)
├── claim_submission             ← header: type, dates, total, patient/provider/insurer refs
├── claim_item × N               ← line items: HCPCS code, unit price, net amount
├── claim_diagnosis × N          ← ICD diagnosis codes
└── claim_care_team × N          ← providers and roles
```

**Parser:** `db/parser.py` — `load_claim()`, `load_claim_item()`, `load_claim_diagnoses()`, `load_claim_care_team()`.

**Populated by:** `FHIRClient.save_claims_to_db()` via `get_anthem_session()`. Upserted by `claim_submission.id`.

#### Member-Submitted Paper Claims (PDF) → 3 tables

NOT from FHIR — user-created records from paper PDF bills (CMS-1500).

```
Paper PDF form (one document)
├── member_claim                 ← form header: submitter, patient, subscriber, provider, dates, totals
├── member_claim_item × N        ← line items: CPT code, description, ICD code, units, amount
└── member_claim_submission      ← portal submission tracking with EOB/claim matching
```

`member_claim_submission` tracks portal-submitted claims with auto-matching: `status` goes `registered → matched → adjudicated`. Matched against `matched_eob_id` and `matched_claim_id` via `services/member_submissions.py::match_registered_submissions()`.

New classification columns on `eob` and `claim_submission`:
- `submission_origin` (`'member'` | `'provider'`) — determined by `classify_submission_origin()`
- `is_out_of_network` (`BOOLEAN`) — determined by `is_out_of_network()`

---

### UCLA FHIR Resources (Clinical)

#### `Encounter` → 2 tables (anchor for all clinical data)

```
Encounter (one resource)
├── encounter                    ← visit header: patient, status, class, date range, reason
└── encounter_participant × N    ← providers who participated
```

**Populated by:** `save_encounters_to_db()` (FHIR) or `EHIImporter.import_encounters()` (EHI `PAT_ENC.tsv`). `source` column distinguishes `'fhir'` vs `'ehi'`; EHI rows get `source_id` = `PAT_ENC_CSN_ID` and synthetic `id` = `ehi_enc_<CSN>`.

#### `DiagnosticReport` + `Observation` → 2 tables

```
DiagnosticReport (one resource) + N Observation children
├── diagnostic_report             ← panel header: category, code (LOINC), date, performer, conclusion
└── lab_result × N               ← individual test results with values, reference ranges, interpretations
```

**Populated by:** `save_labs_to_db()`. Upserted by `diagnostic_report.id`; old `lab_result` children deleted and re-inserted.

Values extracted from `valueQuantity` (numeric), `valueString`, `valueBoolean`, `valueCodeableConcept` in priority order. Reference ranges parsed from `Observation.referenceRange[*]`.

#### `Observation` (standalone) → 2 tables

- `imaging_observation` — standalone imaging measurements (POCUS), no `report_id` link
- `clinical_observation` — generic catch-all for ANY `Observation`: vitals, surveys, imaging, labs-without-panel. Has `category` column and `encounter_id` FK. `fhir_id` is unique-constrained.

#### Single-table resources

| FHIR Resource | Table | Enc. FK | Provenance (`source`) | Notes |
|---|---|---|---|---|
| `Condition` | `condition` | Yes | fhir/ehi | `clinical_status`, `verification_status`, ICD/SNOMED code |
| `Procedure` | `procedure_record` | Yes | fhir only | Named `procedure_record` to avoid SQL reserved word |
| `MedicationStatement` | `medication_statement` | **No** | fhir only | Patient-reported usage, standing record |
| `MedicationRequest` | `medication_request` | Yes | fhir/ehi | EHI from `ORDER_MED.tsv` |
| `MedicationAdministration` | `medication_administration` | Yes | fhir/ehi | EHI from `MAR_ADMIN_INFO.tsv`; has `morphone_mg` column |
| `AllergyIntolerance` | `allergy_intolerance` | **No** | fhir only | Standing record |
| `Immunization` | `immunization` | **No** | fhir/ehi | EHI from `IMM_ADMIN.tsv` |
| `ServiceRequest` | `service_request` | Yes | fhir/ehi | EHI from `ORDER_PROC.tsv` |
| `Specimen` | `specimen` | Yes | fhir only | Lab specimen collection |
| `Communication` | `communication` | Yes | fhir only | Patient-provider messages |
| `CareTeam` | `care_team` | Yes | fhir only | Care coordination teams |
| `CarePlan` | `care_plan` | Yes | fhir only | Treatment plans |
| `DocumentReference` | `document_reference` | Yes | fhir only | Clinical document references |
| `FamilyMemberHistory` | `family_member_history` | **No** | fhir only | Family health conditions |
| `ClinicalNote` | `clinical_note` | Yes | fhir/ehi | Discharge summaries, procedure notes. Stores extracted `text_body` + raw cached `raw_html` Binary blobs. EHI from `HNO_INFO.tsv` + RTF bodies |

#### EHI Synthetic FHIR IDs

EHI-imported rows use namespaced synthetic `fhir_id` values:

| EHI Table | Synthetic fhir_id pattern | Target Table |
|---|---|---|
| `HNO_INFO.tsv` | `ehi_note_<NOTE_ID>` | `clinical_note` |
| `MAR_ADMIN_INFO.tsv` | `ehi_mar_<ORDER_MED_ID>_<LINE>` | `medication_administration` |
| `PAT_ENC.tsv` | `ehi_enc_<CSN>` | `encounter` |
| `IP_FLWSHT_MEAS.tsv` | `ehi_vitals_<FSD_ID>_<LINE>` | `clinical_observation` |
| `ORDER_MED.tsv` | `ehi_medorder_<ORDER_MED_ID>` | `medication_request` |
| `ORDER_PROC.tsv` | `ehi_procorder_<ORDER_PROC_ID>` | `service_request` |
| `IMM_ADMIN.tsv` | `ehi_imm_<DOCUMENT_ID>` | `immunization` |

---

## Database: myhealth_auth

**17 tables + 0 views.** Primary source of truth for OAuth tokens.

### Table: `oauth_tokens`

| Column | Type | PK | Nullable | Default | Description |
|--------|------|-----|----------|---------|-------------|
| `patient_id` | VARCHAR(100) | **PK** | No | — | FHIR patient ID from JWT `sub` claim |
| `provider` | VARCHAR(20) | **PK** | No | — | `anthem` or `ucla` |
| `access_token` | VARCHAR(2000) | — | No | — | Bearer token, ~1h lifetime |
| `token_type` | VARCHAR(20) | — | No | `Bearer` | Token type |
| `expires_in` | INTEGER | — | No | `3600` | Access token lifetime in seconds |
| `scope` | TEXT | — | Yes | — | Space-separated OAuth scopes |
| `refresh_token` | TEXT | — | Yes | — | Refresh token, ~30 day lifetime |
| `id_token` | TEXT | — | Yes | — | JWT with `sub`, `name`, etc. |
| `obtained_at` | TIMESTAMP | — | No | — | Access token obtained time |
| `refresh_token_expires_in` | INTEGER | — | No | `2592000` | Refresh token lifetime in seconds |
| `last_eob_fetch` | TIMESTAMP | — | Yes | `NULL` | Last EOB fetch timestamp |
| `last_claim_fetch` | TIMESTAMP | — | Yes | `NULL` | Last Claim fetch timestamp |
| `created_at` | TIMESTAMP | — | No | `NOW()` | Record creation time |
| `updated_at` | TIMESTAMP | — | No | `NOW()` on update | Last refresh/update |

**Written by:** `TokenStore.save()` during `/api/auth/{provider}/exchange`. Updated by `AuthManager.refresh_token()` and `RefreshDaemon`.

### Table: `patients`

| Column | Type | PK | Nullable | Default |
|--------|------|-----|----------|---------|
| `patient_id` | VARCHAR(100) | **PK** | No | — |
| `provider` | VARCHAR(20) | **PK** | No | — |
| `entity_ref` | VARCHAR(255) | — | Yes | `NULL` |

**Written by:** `TokenStore.save()` during initial OAuth2 exchange when `id_token` present. Name stored in `entity_names`.

### Table: `entity_names`

| Column | Type | PK | Nullable | Default |
|--------|------|-----|----------|---------|
| `entity_ref` | VARCHAR(255) | **PK** | No | — |
| `provider` | VARCHAR(20) | — | No | — |
| `entity_type` | VARCHAR(50) | — | No | — |
| `entity_id` | VARCHAR(200) | — | No | — |
| `name` | TEXT | — | Yes | `NULL` |
| `npi` | VARCHAR(20) | — | Yes | `NULL` |
| `display` | TEXT | — | Yes | `NULL` |
| `loaded_at` | TIMESTAMP | — | No | `NOW()` |

Unique constraint on `(provider, entity_type, entity_id)`.

### Table: `pkce_verifiers`

| Column | Type | PK | Nullable | Default |
|--------|------|-----|----------|---------|
| `provider` | VARCHAR(20) | **PK** | No | — |
| `verifier` | VARCHAR(200) | — | No | — |
| `state` | VARCHAR(100) | — | Yes | `NULL` |
| `created_at` | TIMESTAMP | — | No | `NOW()` |
| `updated_at` | TIMESTAMP | — | No | `NOW()` on update |

**Written by:** `AuthManager.build_authorize_url()`. Consumed and cleared by `AuthManager.exchange_code()`.

### Table: `job_run`

| Column | Type | PK | Nullable | Default | Description |
|--------|------|-----|----------|---------|-------------|
| `id` | INTEGER | **PK** (auto) | No | — | Surrogate key |
| `provider` | VARCHAR(20) | **indexed** | No | — | `anthem` or `ucla` |
| `started_at` | TIMESTAMP | — | No | — | Run start time |
| `finished_at` | TIMESTAMP | — | Yes | `NULL` | Run end time (null while running) |
| `status` | VARCHAR(20) | — | No | `running` | `running` → `success` / `failed` / `partial` |
| `counts` | JSON | — | Yes | `NULL` | `{"totals_before": {...}, "totals_after": {...}, "fetched": int, "new_total": int}` |
| `error` | TEXT | — | Yes | `NULL` | Error message on failure |
| `created_at` | TIMESTAMP | — | No | `NOW()` | Record creation |

**Written by:** `job.run_provider()` — inserts `running` row, updates on completion.

---

## Database: myhealth_anthem

**16 tables + 7 views.**

### Replica Tables (synced from auth)

- `oauth_tokens` — identical to auth, replica for view joins
- `patients` — identical to auth, replica for view joins
- `entity_names` — identical to auth, replica for view joins

### Table: `eob` — Explanation of Benefit

| Column | Type | PK | Nullable | Default | Description |
|--------|------|-----|----------|---------|-------------|
| `id` | TEXT | **PK** | No | — | FHIR resource ID |
| `claim_number` | TEXT | — | Yes | — | Anthem claim number (identifier type `uc`) |
| `status` | TEXT | — | Yes | — | `active`, `balanced`, `cancelled`, etc. |
| `claim_type` | TEXT | — | Yes | — | `Professional`, `Inpatient`, `Outpatient`, etc. |
| `sub_type` | TEXT | — | Yes | — | Subtype (e.g., `Behavioral Health`) |
| `use` | TEXT | — | Yes | — | `claim`, `claim-response`, `preclaim` |
| `outcome` | TEXT | — | Yes | — | `complete`, `error`, `queued` |
| `disposition` | TEXT | — | Yes | — | Free-text adjudication summary |
| `created_date` | DATE | — | Yes | — | EOB creation date |
| `billable_period_start` | DATE | — | Yes | — | Service period start |
| `billable_period_end` | DATE | — | Yes | — | Service period end |
| `patient_ref` | TEXT | — | Yes | — | `anthem:Patient:<id>` |
| `provider_ref` | TEXT | — | Yes | — | Provider canonical ref |
| `insurer_payer_id` | TEXT | — | Yes | — | Insurer payer identifier |
| `coverage_ref` | TEXT | — | Yes | — | Coverage resource ref |
| `payee_type` | TEXT | — | Yes | — | `provider`, `patient`, `secondary` |
| `payee_ref` | TEXT | — | Yes | — | Payee canonical ref |
| `submission_origin` | VARCHAR(20) | — | **No** | `'provider'` | `'member'` or `'provider'` |
| `is_out_of_network` | BOOLEAN | — | **No** | `false` | Out-of-network flag |
| `claim_adjustment_key` | TEXT | — | Yes | — | Prior EOB cross-reference for adjustments |
| `payment_amount` | DOUBLE PRECISION | — | Yes | — | Total payment issued |
| `payment_date` | DATE | — | Yes | — | Payment issue date |
| `payment_type` | TEXT | — | Yes | — | `Complete`, `Partial`, `Interest` |
| `last_updated` | TIMESTAMP | — | Yes | — | `meta.lastUpdated` |
| `raw_json` | TEXT | — | Yes | — | Full FHIR JSON |
| `loaded_at` | TIMESTAMP | — | No | `NOW()` | Load time |

**Relationships:**
- `eob.care_team.eob_id` → `eob.id` (1:N)
- `eob.diagnosis.eob_id` → `eob.id` (1:N)
- `eob.item.eob_id` → `eob.id` (1:N)
- `eob.total.eob_id` → `eob.id` (1:N)

### Table: `eob_item` — Line Items

| Column | Type | PK | Nullable | FK | Description |
|--------|------|-----|----------|----|-------------|
| `id` | INTEGER | **PK** (auto) | No | — | Surrogate key |
| `eob_id` | TEXT | — | No | `eob.id` | Parent EOB |
| `sequence` | INTEGER | — | Yes | — | Item position on claim |
| `hcpcs_code` | VARCHAR(10) | — | Yes | — | HCPCS/CPT code |
| `hcpcs_display` | TEXT | — | Yes | — | Code description |
| `modifier_codes` | VARCHAR(100) | — | Yes | — | Comma-separated modifiers |
| `serviced_date` | DATE | — | Yes | — | Date of service |
| `serviced_period_start` | DATE | — | Yes | — | Multi-day start |
| `serviced_period_end` | DATE | — | Yes | — | Multi-day end |
| `location_code` | VARCHAR(10) | — | Yes | — | Place of service code |
| `location_display` | TEXT | — | Yes | — | Location name |
| `quantity` | INTEGER | — | Yes | — | Units billed |
| `net_amount` | DOUBLE PRECISION | — | Yes | — | Net after adjustments |
| `submitted_amount` | DOUBLE PRECISION | — | Yes | — | Billed amount |
| `allowed_amount` | DOUBLE PRECISION | — | Yes | — | Maximum plan-allowed |
| `paid_provider` | DOUBLE PRECISION | — | Yes | — | Insurer-to-provider payment |
| `paid_patient` | DOUBLE PRECISION | — | Yes | — | Patient responsibility |
| `deductible` | DOUBLE PRECISION | — | Yes | — | Deductible applied |
| `coinsurance` | DOUBLE PRECISION | — | Yes | — | Coinsurance portion |
| `copay` | DOUBLE PRECISION | — | Yes | — | Fixed copay |
| `noncovered` | DOUBLE PRECISION | — | Yes | — | Not covered by plan |
| `discount` | DOUBLE PRECISION | — | Yes | — | Contractual discount |
| `member_liability` | DOUBLE PRECISION | — | Yes | — | Total patient responsibility |
| `allowed_units` | INTEGER | — | Yes | — | Covered units |
| `adjustment_reason` | TEXT | — | Yes | — | Adjustment reason text |
| `payment_status` | VARCHAR(20) | — | Yes | — | `BG`, `CL`, etc. |

### Table: `eob_item_adjudication` — Raw Adjudication Entries

| Column | Type | PK | Nullable | FK |
|--------|------|-----|----------|----|
| `id` | INTEGER | **PK** (auto) | No | — |
| `eob_item_id` | INTEGER | — | No | `eob_item.id` |
| `category` | TEXT | — | Yes | — |
| `category_code` | TEXT | — | Yes | — |
| `amount` | DOUBLE PRECISION | — | Yes | — |
| `value_units` | INTEGER | — | Yes | — |
| `reason_code` | VARCHAR(20) | — | Yes | — |
| `reason_display` | TEXT | — | Yes | — |

### Table: `eob_diagnosis`

| Column | Type | PK | Nullable | FK |
|--------|------|-----|----------|----|
| `id` | INTEGER | **PK** (auto) | No | — |
| `eob_id` | TEXT | — | No | `eob.id` |
| `sequence` | INTEGER | — | Yes | — |
| `icd_code` | VARCHAR(15) | — | Yes | — |
| `icd_display` | TEXT | — | Yes | — |
| `diagnosis_type` | VARCHAR(200) | — | Yes | — |
| `on_admission` | VARCHAR(200) | — | Yes | — |

### Table: `eob_care_team`

| Column | Type | PK | Nullable | FK |
|--------|------|-----|----------|----|
| `id` | INTEGER | **PK** (auto) | No | — |
| `eob_id` | TEXT | — | No | `eob.id` |
| `sequence` | INTEGER | — | Yes | — |
| `provider_ref` | TEXT | — | Yes | — |
| `role_code` | VARCHAR(20) | — | Yes | — |
| `role_display` | TEXT | — | Yes | — |

### Table: `eob_total`

| Column | Type | PK | Nullable | FK |
|--------|------|-----|----------|----|
| `id` | INTEGER | **PK** (auto) | No | — |
| `eob_id` | TEXT | — | No | `eob.id` |
| `category` | TEXT | — | Yes | — |
| `category_code` | VARCHAR(20) | — | Yes | — |
| `amount` | DOUBLE PRECISION | — | Yes | — |
| `currency` | VARCHAR(3) | — | Yes | — |

### Table: `claim_submission` — Provider-Submitted Claims

| Column | Type | PK | Nullable | Default | Description |
|--------|------|-----|----------|---------|-------------|
| `id` | TEXT | **PK** | No | — | FHIR Claim ID |
| `status` | TEXT | — | Yes | — | `active`, `cancelled`, `entered-in-error` |
| `claim_type` | TEXT | — | Yes | — | `Professional`, `Institutional`, etc. |
| `use` | TEXT | — | Yes | — | `claim`, `preclaim` |
| `created_date` | DATE | — | Yes | — | Claim creation date |
| `billable_period_start` | DATE | — | Yes | — | Services period start |
| `billable_period_end` | DATE | — | Yes | — | Services period end |
| `patient_ref` | TEXT | — | Yes | — | Patient canonical ref |
| `provider_ref` | TEXT | — | Yes | — | Provider canonical ref |
| `insurer_ref` | TEXT | — | Yes | — | Insurer canonical ref |
| `priority` | TEXT | — | Yes | — | `normal`, `urgent`, `routine` |
| `total_amount` | DOUBLE PRECISION | — | Yes | — | Sum of line items |
| `total_currency` | VARCHAR(3) | — | No | `USD` | Currency code |
| `submission_origin` | VARCHAR(20) | — | **No** | `'provider'` | `'member'` or `'provider'` |
| `is_out_of_network` | BOOLEAN | — | **No** | `false` | Out-of-network flag |
| `last_updated` | TIMESTAMP | — | Yes | — | `meta.lastUpdated` |
| `raw_json` | TEXT | — | Yes | — | Full FHIR JSON |
| `loaded_at` | TIMESTAMP | — | No | `NOW()` | Load time |

**Relationships:**
- `claim_item.claim_id` → `claim_submission.id` (1:N)
- `claim_diagnosis.claim_id` → `claim_submission.id` (1:N)
- `claim_care_team.claim_id` → `claim_submission.id` (1:N)

### Table: `claim_item`

| Column | Type | PK | Nullable | FK |
|--------|------|-----|----------|----|
| `id` | INTEGER | **PK** (auto) | No | — |
| `claim_id` | TEXT | — | No | `claim_submission.id` |
| `sequence` | INTEGER | — | Yes | — |
| `hcpcs_code` | VARCHAR(10) | — | Yes | — |
| `hcpcs_display` | TEXT | — | Yes | — |
| `modifier_codes` | VARCHAR(100) | — | Yes | — |
| `serviced_date` | DATE | — | Yes | — |
| `serviced_period_start` | DATE | — | Yes | — |
| `serviced_period_end` | DATE | — | Yes | — |
| `location_code` | VARCHAR(10) | — | Yes | — |
| `location_display` | TEXT | — | Yes | — |
| `quantity` | INTEGER | — | Yes | — |
| `unit_price` | DOUBLE PRECISION | — | Yes | — |
| `net_amount` | DOUBLE PRECISION | — | Yes | — |

### Table: `claim_diagnosis`

| Column | Type | PK | Nullable | FK |
|--------|------|-----|----------|----|
| `id` | INTEGER | **PK** (auto) | No | — |
| `claim_id` | TEXT | — | No | `claim_submission.id` |
| `sequence` | INTEGER | — | Yes | — |
| `icd_code` | VARCHAR(15) | — | Yes | — |
| `icd_display` | TEXT | — | Yes | — |
| `diagnosis_type` | VARCHAR(200) | — | Yes | — |

### Table: `claim_care_team`

| Column | Type | PK | Nullable | FK |
|--------|------|-----|----------|----|
| `id` | INTEGER | **PK** (auto) | No | — |
| `claim_id` | TEXT | — | No | `claim_submission.id` |
| `sequence` | INTEGER | — | Yes | — |
| `provider_ref` | TEXT | — | Yes | — |
| `role_code` | VARCHAR(20) | — | Yes | — |
| `role_display` | TEXT | — | Yes | — |

### Table: `member_claim` — Paper Claim Forms

| Column | Type | PK | Nullable | Default |
|--------|------|-----|----------|---------|
| `id` | INTEGER | **PK** (auto) | No | — |
| `submitter_ref` | VARCHAR(255) | — | Yes | `NULL` |
| `submitted_date` | DATE | — | Yes | `NULL` |
| `patient_ref` | VARCHAR(255) | — | Yes | `NULL` |
| `patient_dob` | DATE | — | Yes | `NULL` |
| `patient_relationship` | VARCHAR(50) | — | Yes | `NULL` |
| `patient_gender` | VARCHAR(10) | — | Yes | `NULL` |
| `has_other_insurance` | BOOLEAN | — | Yes | `NULL` |
| `subscriber_ref` | VARCHAR(255) | — | Yes | `NULL` |
| `subscriber_id` | VARCHAR(50) | — | Yes | `NULL` |
| `subscriber_group` | VARCHAR(50) | — | Yes | `NULL` |
| `subscriber_dob` | DATE | — | Yes | `NULL` |
| `provider_ref` | VARCHAR(255) | — | Yes | `NULL` |
| `provider_tax_id` | VARCHAR(20) | — | Yes | `NULL` |
| `provider_npi` | VARCHAR(20) | — | Yes | `NULL` |
| `place_of_service` | VARCHAR(10) | — | Yes | `NULL` |
| `job_related` | BOOLEAN | — | Yes | `NULL` |
| `invoice_number` | VARCHAR(50) | — | Yes | `NULL` |
| `invoice_date` | DATE | — | Yes | `NULL` |
| `date_of_service` | DATE | — | Yes | `NULL` |
| `referring_provider_ref` | VARCHAR(255) | — | Yes | `NULL` |
| `primary_icd_code` | VARCHAR(15) | — | Yes | `NULL` |
| `primary_icd_display` | TEXT | — | Yes | `NULL` |
| `invoice_total` | DOUBLE PRECISION | — | Yes | `NULL` |
| `payments_credits` | DOUBLE PRECISION | — | Yes | `NULL` |
| `balance_due` | DOUBLE PRECISION | — | Yes | `NULL` |
| `source_pdf` | VARCHAR(255) | — | Yes | `NULL` |
| `notes` | TEXT | — | Yes | `NULL` |
| `created_at` | TIMESTAMP | — | No | `NOW()` |

### Table: `member_claim_item`

| Column | Type | PK | Nullable | FK |
|--------|------|-----|----------|----|
| `id` | INTEGER | **PK** (auto) | No | — |
| `claim_id` | INTEGER | — | No | `member_claim.id` |
| `cpt_code` | VARCHAR(10) | — | Yes | — |
| `cpt_description` | TEXT | — | Yes | — |
| `icd_code` | VARCHAR(15) | — | Yes | — |
| `modifier` | VARCHAR(20) | — | Yes | — |
| `units` | INTEGER | — | Yes | — |
| `amount` | DOUBLE PRECISION | — | Yes | — |

### Table: `member_claim_submission` — Portal Submission Tracking

| Column | Type | PK | Nullable | Default |
|--------|------|-----|----------|---------|
| `id` | INTEGER | **PK** (auto) | No | — |
| `portal_submission_id` | VARCHAR(200) | — | Yes | `NULL` |
| `patient_id` | VARCHAR(200) | — | Yes | `NULL` |
| `claim_number` | TEXT | — | Yes | `NULL` |
| `provider_ref` | VARCHAR(255) | — | Yes | `NULL` |
| `provider_npi` | VARCHAR(20) | — | Yes | `NULL` |
| `service_date` | DATE | — | Yes | `NULL` |
| `cpt_codes` | TEXT | — | Yes | `NULL` |
| `total_amount` | DOUBLE PRECISION | — | Yes | `NULL` |
| `status` | VARCHAR(20) | — | **No** | `'registered'` |
| `matched_eob_id` | TEXT | — | Yes | `NULL` |
| `matched_claim_id` | TEXT | — | Yes | `NULL` |
| `created_at` | TIMESTAMP | — | No | `NOW()` |
| `updated_at` | TIMESTAMP | — | No | `NOW()` on update |

`status` values: `registered` → `matched` → `adjudicated`

---

## Database: myhealth_ucla

**24 tables + 2 views.**

### Replica Tables

`oauth_tokens`, `patients`, `entity_names` — replicas synced from `myhealth_auth`.

### Table: `encounter` — Visit Anchor

| Column | Type | PK | Nullable | Default | Description |
|--------|------|-----|----------|---------|-------------|
| `id` | TEXT | **PK** | No | — | FHIR ID or synthetic `ehi_enc_<CSN>` |
| `patient_id` | VARCHAR(100) | — | Yes | — | FHIR patient ID |
| `patient_ref` | VARCHAR(255) | — | Yes | — | Canonical patient ref |
| `status` | VARCHAR(20) | — | Yes | — | `finished`, `in-progress`, `planned` |
| `class_` | VARCHAR(50) | — | Yes | — | `ambulatory`, `emergency`, `inpatient` |
| `subject_display` | TEXT | — | Yes | — | Patient subject display |
| `period_start` | TIMESTAMP | — | Yes | — | Visit start |
| `period_end` | TIMESTAMP | — | Yes | — | Visit end |
| `date` | TIMESTAMP | — | Yes | — | Effective date (no `period` cases) |
| `reason_code` | VARCHAR(20) | — | Yes | — | SNOMED/ICD reason code |
| `reason_display` | TEXT | — | Yes | — | Human reason |
| `location` | TEXT | — | Yes | — | Visit location |
| `source` | VARCHAR(10) | — | Yes | `'fhir'` | Provenance: `fhir` or `ehi` |
| `source_id` | TEXT | — | Yes | — | EHI `PAT_ENC_CSN_ID` |
| `raw_json` | TEXT | — | Yes | — | Full FHIR JSON |
| `loaded_at` | TIMESTAMP | — | No | `NOW()` | Load time |

**All FKs from clinical tables point to `encounter.id`:**
`encounter_participant`, `diagnostic_report`, `clinical_observation`, `clinical_note`, `condition`, `procedure_record`, `medication_request`, `medication_administration`, `service_request`, `specimen`, `communication`, `care_team`, `care_plan`, `document_reference`

### Table: `encounter_participant`

| Column | Type | PK | Nullable | FK |
|--------|------|-----|----------|----|
| `id` | INTEGER | **PK** (auto) | No | — |
| `encounter_id` | TEXT | — | No | `encounter.id` |
| `individual_ref` | TEXT | — | Yes | — |
| `role_code` | VARCHAR(20) | — | Yes | — |
| `role_display` | TEXT | — | Yes | — |

### Table: `diagnostic_report` — Lab Panels

| Column | Type | PK | Nullable | Default | Description |
|--------|------|-----|----------|---------|-------------|
| `id` | TEXT | **PK** | No | — | FHIR ID |
| `patient_id` | VARCHAR(100) | — | Yes | — | Patient FHIR ID |
| `provider` | VARCHAR(20) | — | Yes | `ucla` | Source provider |
| `status` | VARCHAR(20) | — | Yes | — | `preliminary`, `final`, `amended` |
| `category` | VARCHAR(100) | — | Yes | — | Comma-separated: `laboratory`, `imaging` |
| `code_display` | TEXT | — | Yes | — | Panel name |
| `code_text` | TEXT | — | Yes | — | Short text code |
| `code_loinc` | VARCHAR(20) | — | Yes | — | LOINC code |
| `effective_datetime` | TIMESTAMP | — | Yes | — | Report date |
| `issued` | TIMESTAMP | — | Yes | — | Issued date |
| `performer_ref` | VARCHAR(255) | — | Yes | — | Lab/facility ref |
| `conclusion` | TEXT | — | Yes | — | Clinician conclusion |
| `body_site` | TEXT | — | Yes | — | Body site (imaging) |
| `method` | TEXT | — | Yes | — | Testing method |
| `encounter_id` | TEXT | — | Yes | `encounter.id` | FK to visit |
| `specimen_ref` | TEXT | — | Yes | — | Specimen reference |
| `has_images` | INTEGER | — | Yes | `0` | Has imaging results |
| `raw_json` | TEXT | — | Yes | — | Full FHIR JSON |
| `loaded_at` | TIMESTAMP | — | No | `NOW()` | Load time |

### Table: `lab_result` — Individual Tests

| Column | Type | PK | Nullable | FK | Description |
|--------|------|-----|----------|----|-------------|
| `id` | INTEGER | **PK** (auto) | No | — | Surrogate key |
| `fhir_id` | TEXT | — | No | — | Original FHIR Observation ID |
| `report_id` | TEXT | — | No | `diagnostic_report.id` | Parent panel |
| `code_display` | TEXT | — | Yes | — | Test name |
| `code_text` | TEXT | — | Yes | — | Short test name |
| `code_loinc` | VARCHAR(20) | — | Yes | — | LOINC code |
| `value` | TEXT | — | Yes | — | Formatted value (e.g. `"96 mg/dL"`) |
| `value_float` | DOUBLE PRECISION | — | Yes | — | Numeric value |
| `value_unit` | VARCHAR(20) | — | Yes | — | Unit |
| `reference_range` | TEXT | — | Yes | — | Normal range |
| `interpretation_code` | VARCHAR(10) | — | Yes | — | `H`, `L`, `N`, `HH`, `LL` |
| `interpretation_display` | VARCHAR(30) | — | Yes | — | Interpretation text |
| `effective_datetime` | TIMESTAMP | — | Yes | — | Observation date |
| `status` | VARCHAR(20) | — | Yes | — | `final`, `preliminary` |
| `component_value` | TEXT | — | Yes | — | JSON array of components |
| `raw_json` | TEXT | — | Yes | — | Full FHIR JSON |
| `loaded_at` | TIMESTAMP | — | No | `NOW()` | Load time |

### Table: `imaging_observation` — Standalone Imaging

Same structure as `lab_result` minus `report_id`. `fhir_id` is NOT unique-constrained here.

### Table: `clinical_observation` — Generic Observations

| Column | Type | PK | Nullable | Default | Description |
|--------|------|-----|----------|---------|-------------|
| `id` | INTEGER | **PK** (auto) | No | — | — |
| `fhir_id` | TEXT | **UNIQUE** | No | — | FHIR Observation ID |
| `encounter_id` | TEXT | — | Yes | `encounter.id` | FK to visit |
| `category` | VARCHAR(50) | — | Yes | — | `laboratory`, `vital-signs`, `imaging`, `survey` |
| `code_display` | TEXT | — | Yes | — | Observation name |
| `code_text` | TEXT | — | Yes | — | Short code text |
| `code_system` | TEXT | — | Yes | — | Coding system URI |
| `code_loinc` | VARCHAR(20) | — | Yes | — | LOINC code |
| `value_text` | TEXT | — | Yes | — | Human-readable value |
| `value_float` | DOUBLE PRECISION | — | Yes | — | Numeric value |
| `value_unit` | VARCHAR(30) | — | Yes | — | Unit |
| `reference_range` | TEXT | — | Yes | — | Normal range |
| `interpretation_code` | VARCHAR(10) | — | Yes | — | `H`, `L`, `N` |
| `interpretation_display` | VARCHAR(30) | — | Yes | — | Interpretation |
| `effective_datetime` | TIMESTAMP | — | Yes | — | Date |
| `status` | VARCHAR(20) | — | Yes | — | Status |
| `component_value` | TEXT | — | Yes | — | JSON components |
| `source` | VARCHAR(10) | — | No | `'fhir'` | `fhir` or `ehi` |
| `source_id` | TEXT | — | Yes | — | EHI synthetic ID |
| `raw_json` | TEXT | — | Yes | — | Full JSON |
| `loaded_at` | TIMESTAMP | — | No | `NOW()` | — |

### Clinical Tables (one per FHIR resource type)

All the following follow a similar pattern: `fhir_id` as PK, `patient_id`, optional `encounter_id` FK, status/type fields, coding fields (`code_system`, `code_value`, `code_display`), timestamps, provenance columns, full `raw_json`, and `loaded_at`.

| Table | PK | Encounter FK | `source` col | EHI Backfill |
|---|---|---|---|---|
| `condition` | `fhir_id` | Yes | Yes | No |
| `procedure_record` | `fhir_id` | Yes | No | No |
| `medication_statement` | `fhir_id` | **No** | No | No |
| `medication_request` | `fhir_id` | Yes | Yes | `ORDER_MED.tsv` |
| `medication_administration` | `id` (auto), `fhir_id` (unique) | Yes | Yes | `MAR_ADMIN_INFO.tsv` |
| `allergy_intolerance` | `fhir_id` | **No** | No | No |
| `immunization` | `fhir_id` | **No** | Yes | `IMM_ADMIN.tsv` |
| `service_request` | `fhir_id` | Yes | Yes | `ORDER_PROC.tsv` |
| `specimen` | `fhir_id` | Yes | Yes | No |
| `communication` | `id` (auto), `fhir_id` (unique) | Yes | Yes | No |
| `care_team` | `fhir_id` | Yes | Yes | No |
| `care_plan` | `fhir_id` | Yes | No | No |
| `document_reference` | `fhir_id` | Yes | No | No |
| `family_member_history` | `fhir_id` | **No** | No | No |
| `clinical_note` | `id` (auto), `fhir_id` (unique) | Yes | Yes | `HNO_INFO.tsv` + RTF |

Detailed column lists are in the expanded section below.

### Detailed: `clinical_note`

| Column | Type | PK/Constraint | Nullable | Default |
|--------|------|---|----------|---------|
| `id` | INTEGER | PK (auto) | No | — |
| `fhir_id` | TEXT | UNIQUE | No | — |
| `encounter_id` | TEXT | FK `encounter.id` | Yes | — |
| `resource_type` | VARCHAR(30) | — | Yes | — |
| `category` | VARCHAR(50) | — | Yes | — |
| `title` | TEXT | — | Yes | — |
| `text_body` | TEXT | — | Yes | — |
| `raw_html` | TEXT | — | Yes | — |
| `raw_rtf` | TEXT | — | Yes | — |
| `author_ref` | TEXT | — | Yes | — |
| `authored_datetime` | TIMESTAMP | — | Yes | — |
| `effective_datetime` | TIMESTAMP | — | Yes | — |
| `source` | VARCHAR(10) | — | No | `'fhir'` |
| `source_id` | TEXT | — | Yes | — |
| `raw_json` | TEXT | — | Yes | — |
| `loaded_at` | TIMESTAMP | — | No | `NOW()` |

### Detailed: `medication_administration`

| Column | Type | PK/Constraint | Nullable | Default |
|--------|------|---|----------|---------|
| `id` | INTEGER | PK (auto) | No | — |
| `fhir_id` | TEXT | UNIQUE | No | — |
| `source` | VARCHAR(10) | — | No | `'fhir'` |
| `source_id` | TEXT | — | Yes | — |
| `order_id` | TEXT | — | Yes | — |
| `patient_id` | VARCHAR(100) | — | Yes | — |
| `encounter_id` | TEXT | FK `encounter.id` | Yes | — |
| `status` | VARCHAR(30) | — | Yes | — |
| `medication_display` | TEXT | — | Yes | — |
| `administered_datetime` | TIMESTAMP | — | Yes | — |
| `scheduled_datetime` | TIMESTAMP | — | Yes | — |
| `route_display` | VARCHAR(50) | — | Yes | — |
| `dose_display` | TEXT | — | Yes | — |
| `dose_quantity` | DOUBLE PRECISION | — | Yes | — |
| `dose_unit` | VARCHAR(30) | — | Yes | — |
| `morphone_mg` | DOUBLE PRECISION | — | Yes | — |
| `performer_ref` | VARCHAR(255) | — | Yes | — |
| `reason_display` | TEXT | — | Yes | — |
| `note_text` | TEXT | — | Yes | — |
| `raw_json` | TEXT | — | Yes | — |
| `loaded_at` | TIMESTAMP | — | No | `NOW()` |

### Detailed: `condition`

| Column | Type | PK | Nullable | Default |
|--------|------|-----|----------|---------|
| `fhir_id` | TEXT | PK | No | — |
| `patient_id` | VARCHAR(100) | — | Yes | — |
| `encounter_id` | TEXT | FK | Yes | — |
| `clinical_status` | VARCHAR(30) | — | Yes | — |
| `verification_status` | VARCHAR(30) | — | Yes | — |
| `category` | VARCHAR(50) | — | Yes | — |
| `code_system` | TEXT | — | Yes | — |
| `code_value` | VARCHAR(30) | — | Yes | — |
| `code_display` | TEXT | — | Yes | — |
| `body_site` | TEXT | — | Yes | — |
| `severity_text` | VARCHAR(30) | — | Yes | — |
| `onset_datetime` | TIMESTAMP | — | Yes | — |
| `abatement_datetime` | TIMESTAMP | — | Yes | — |
| `recorded_date` | TIMESTAMP | — | Yes | — |
| `asserter_ref` | VARCHAR(255) | — | Yes | — |
| `note_text` | TEXT | — | Yes | — |
| `source` | VARCHAR(10) | — | No | `'fhir'` |
| `source_id` | TEXT | — | Yes | — |
| `raw_json` | TEXT | — | Yes | — |
| `loaded_at` | TIMESTAMP | — | No | `NOW()` |

---

## Denormalized Views

### myhealth_anthem views (7)

#### `eob_claims`
Denormalized EOB with patient name, provider name, payee name, and aggregated ICD codes, care team, and totals.

**Columns:** `eob_id`, `status`, `claim_type`, `sub_type`, `outcome`, `created_date`, `billable_period_start/end`, `patient_id`, `patient_name`, `provider`, `provider_name`, `payee_name`, `claim_number`, `payment_amount`, `payment_date`, `last_updated`, `icd_codes` (comma-separated), `icd_displays` (pipe-separated), `care_team_providers` (comma-separated), `care_team_roles`, `total_submitted`, `total_deductible`, `total_benefit`

**SQL:** `eob` LEFT JOIN `oauth_tokens`/`patients`/`entity_names` (×3) + correlated subqueries on `eob_diagnosis`, `eob_care_team`, `eob_total`.

#### `eob_items`
EOB line items with parent EOB context.

**Columns:** `eob_id`, `item_seq`, `hcpcs_code`, `hcpcs_display`, `modifier_codes`, `serviced_date`, `serviced_period_start/end`, `location_code/display`, `quantity`, all adjudication columns (renamed with `item_` prefix: `item_deductible`, `item_coinsurance`, etc.), plus patient/EOB context columns.

**SQL:** `eob_item` JOIN `eob` LEFT JOIN token/patient/entity tables.

#### `claim_submissions`
Denormalized claims with patient name, provider name, insurer name, diagnoses (aggregated), care team (aggregated).

**SQL:** `claim_submission` LEFT JOIN token/patient/entity tables (×3) + correlated subqueries on `claim_diagnosis`, `claim_care_team`.

#### `claim_items`
Claim line items with parent claim context.

**SQL:** `claim_item` JOIN `claim_submission` LEFT JOIN token/patient/entity tables.

#### `member_claims_recon`
Reconciliation view: LEFT JOINs `member_claim_item` against `eob_item` matching on `hcpcs_code` = `cpt_code` AND `serviced_date` = `date_of_service`. Shows `recon_status` (`'adjudicated'` vs `'pending'`).

**SQL:** `member_claim` JOIN `member_claim_item` LEFT JOIN `eob_item` on code+date LEFT JOIN `eob` LEFT JOIN entity_names ×5.

#### `member_claims_summary`
Aggregated reconciliation grouped by paper claim.

**Columns:** `member_claim_id`, `patient_id`, `submitted_date`, `patient_name`, `provider_name`, `provider_npi`, `date_of_service`, `invoice_total`, `payments_credits`, `balance_due`, `line_items` (count), `total_submitted_amount`, `adjudicated_items`, `pending_items`, `total_paid_by_anthem`, `total_member_liability`.

#### `member_claims`
Unified view of all member-submitted and out-of-network claims. UNION ALL of:
1. EOBs where `submission_origin='member'` OR `is_out_of_network=true`
2. `member_claim_submission` LEFT JOIN `eob`

**Columns:** `source` (`'fhir'` or `'registry'`), `eob_id`, `claim_number`, `status`, `outcome`, `created_date`, `patient_id`, `patient_name`, `provider_name`, `payee_name`, `submission_origin`, `is_out_of_network`, `total_submitted`, `total_member_liability`, `total_noncovered`, plus submission tracking columns.

---

### myhealth_ucla views (2)

#### `lab_results`
Flattens `diagnostic_report` + `lab_result` into one row per test result with panel context.

**SQL:** `diagnostic_report` JOIN `lab_result` LEFT JOIN `entity_names` (performer).

**Columns:** `panel_id`, `patient_id`, `provider`, `panel_status`, `panel_name`, `panel_loinc`, `panel_date`, `lab_name`, `result_id`, `observation_id`, `test_name`, `test_short`, `test_loinc`, `result_value`, `result_numeric`, `value_unit`, `reference_range`, `interpretation_code/display`, `result_date`, `result_status`, `panel_loaded_at`, `result_loaded_at`.

#### `clinical_overview`
Encounter-level summary with counts of associated data and date ranges.

**SQL:** `encounter` LEFT JOIN `entity_names` (patient), `diagnostic_report`, `lab_result`, `clinical_observation`, `clinical_note` → GROUP BY encounter.

**Columns:** `encounter_id`, `patient_id`, `patient_name`, `encounter_status`, `encounter_class`, `period_start/end`, `reason_display`, `location`, `lab_panels_count`, `lab_results_count`, `clinical_observations_count`, `notes_count`, `earliest_result_date`, `latest_result_date`.

---

## API Endpoints

All routes under `/api` prefix, defined in `backend/src/myhealth_fhir/api/dashboard.py`.

### `GET /api/status`
Per-provider dashboard status with last run info, row counts, and token health.

**Tables read:**
- `job_run` — last run per provider via `_last_run()`
- All tables in `_COUNTERS` per provider — raw `SELECT COUNT(*)` for totals
- `oauth_tokens`, `patients`, `entity_names` — token health via `AuthManager.status()`

**Response structure:**
```json
{
  "anthem": { "lastRun": { ...job_run row... }, "totals": { "eob": N, ... }, "tokens": [ ... ] },
  "ucla":   { "lastRun": { ...job_run row... }, "totals": { "encounter": N, ... }, "tokens": [ ... ] }
}
```

### `GET /api/auth/{provider}/start`
Builds OAuth authorize URL with PKCE flow. Persists PKCE verifier.

**Tables written:** `pkce_verifiers` (upsert via `AuthManager.build_authorize_url()`).

**Response:** `{"url": "https://...authorize?..."}`

### `POST /api/auth/{provider}/exchange`
Exchanges pasted redirect URL authorization code for tokens.

**Body:** `{"redirect_url": "https://localhost/callback?code=XXX&state=YYY"}`

**Tables written:**
- `oauth_tokens` — upsert by `(patient_id, provider)` via `TokenStore.save()`
- `patients` — upsert patient identity
- `entity_names` — upsert patient name
- Then `_sync_oauth_tokens_replica()` copies to anthem/ucla DBs

**Response:** `{"status": "ok", "patientId": "...", "patientName": "..."}`

### `GET /api/providers`
Lists configured provider names and kinds.

**No DB access.** Returns from `config/settings.py` `PROVIDERS` dict.

---

## CLI Commands

All prefixed: `uv run --project backend myhealth <cmd>`

| Command | Tables Read | Tables Written | Description |
|---------|------|------|-------------|
| `myhealth job` | — | `job_run`, all provider tables | Single run of all providers |
| `myhealth job -p anthem` | — | `job_run`, anthem tables | Single provider run |
| `myhealth job --daemon --interval-minutes N` | — | `job_run`, all provider tables | Daemon mode with RefreshDaemon |
| `myhealth job --skip-labs` | — | `job_run`, non-lab UCLA tables | Skip DiagnosticReport/Observation |
| `myhealth anthem auth status` | `oauth_tokens`, `patients` | — | Token health for anthem |
| `myhealth anthem eob` | tokens | `eob`, `eob_item`, `eob_diagnosis`, etc. | Fetch EOBs |
| `myhealth anthem eob --no-db` | tokens | — (stdout only) | Fetch and print, no DB write |
| `myhealth ucla save-labs` | tokens | `diagnostic_report`, `lab_result` | Fetch labs |
| `myhealth ucla save-labs --no-db` | tokens | — (stdout only) | Fetch and print, no DB write |
| `myhealth ucla save-all --wipe` | tokens | Truncates all UCLA clinical tables then re-fetches | Full re-sync |
| `myhealth ucla ehi <dir> -p <FHIR_PATIENT_ID>` | `encounter` (read) | `clinical_note`, `medication_administration`, `encounter`, `clinical_observation`, `medication_request`, `service_request`, `immunization` | EHI import |
| `myhealth ucla ehi <dir> -p <ID> --only notes` | `encounter` (read) | `clinical_note` only | Selective EHI import |
| `myhealth ucla ehi <dir> -p <ID> --no-db` | `encounter` (read) | — (stdout only) | Dry-run EHI import |

---

## Job System

**File:** `backend/src/myhealth_fhir/job/__init__.py`

### Table Counters

```python
_COUNTERS = {
    "anthem": ["eob", "claim_submission", "member_claim"],
    "ucla": ["encounter", "diagnostic_report", "lab_result", "imaging_observation",
             "clinical_observation", "clinical_note", "medication_administration",
             "service_request", "specimen", "communication", "care_team"],
}
```

These tables are `SELECT COUNT(*)`'d before and after each run to produce deltas in `job_run.counts`.

### Execution Flow

```
run_all(providers)
  └── run_provider("anthem")
        ├── INSERT job_run (status='running')
        ├── _count_rows(anthem)  → totals_before
        ├── FHIRClient.fetch_and_store_eobs_all_patients()
        │     └── save_eobs_to_db() → eob + children + entity_names + patients
        ├── FHIRClient.fetch_and_store_claims_all_patients()
        │     └── save_claims_to_db() → claim_submission + children + entity_names
        ├── _count_rows(anthem)  → totals_after
        └── UPDATE job_run (status='success','failed','partial', counts=JSON, error=...)
  └── run_provider("ucla")
        ├── INSERT job_run (status='running')
        ├── _count_rows(ucla)
        ├── FHIRClient.fetch_and_store_labs_all_patients()
        │     └── save_labs_to_db() → diagnostic_report + lab_result
        ├── FHIRClient.fetch_and_store_all_clinical_data(skip_labs)
        │     └── save_*_to_db() → all clinical tables
        ├── _count_rows(ucla)
        └── UPDATE job_run (status=...)
```

### Startup via `main.py`

```python
async def lifespan(app):
    init_db()  # Creates all tables and views (idempotent)
    start_job_thread(interval_minutes)  # Background daemon thread
```

`init_db()` calls `create_all()` on each base (AuthBase, AnthemBase, UclaBase) plus SQL `CREATE VIEW` statements. All idempotent.

---

## ORM Model Files

All in `backend/src/myhealth_fhir/db/models/`:

| File | Base | Models |
|------|------|--------|
| `models_auth.py` | `AuthBase` | `OAuthTokenRecord`, `EntityName`, `PatientRecord`, `PKCEVerifier`, `JobRun` |
| `models_anthem.py` | `AnthemBase` | `EOB` (+children), `ClaimSubmission` (+children), `MemberClaim`, `MemberClaimItem`, `MemberClaimSubmission`, `EntityName`, `OAuthTokenRecord`, `PatientRecord` |
| `models_ucla.py` | `UclaBase` | `Encounter`, `DiagnosticReport`, `LabResult`, all clinical models, replica tables |

Session factories in `backend/src/myhealth_fhir/db/__init__.py`.

---

## EHI Import System

**File:** `backend/src/myhealth_fhir/services/ehi_importer.py`

**CLI:** `myhealth ucla ehi <export-dir> -p <FHIR_PATIENT_ID>`

Imports Epic EHI (Export of Health Information) TSV data into UCLA tables. Always carries `source='ehi'`.

### Import Order (important for FK safety)
1. **Encounters first** — `import_encounters()` reads `PAT_ENC.tsv`, backfills `encounter` table with `source_id=CSN`
2. **Notes** — `import_notes()` reads `HNO_INFO.tsv` + `Rich Text/HNO_<ID>_<CSN>_41.rtf`; joins encounter via CSN map
3. **Meds, vitals, orders** — all other imports, linking encounters via CSN

### EHI Tables Mapped

| EHI TSV File | Target Table | EHI ID Column | Synthetic fhir_id | Notes |
|---|---|---|---|---|
| `PAT_ENC.tsv` | `encounter` | `PAT_ENC_CSN_ID` | `ehi_enc_<CSN>` | Skips CSNs already in FK map |
| `HNO_INFO.tsv` | `clinical_note` | `NOTE_ID` | `ehi_note_<id>` | Deleted notes (`DELETE_INSTANT_DTTM`) skipped |
| `MAR_ADMIN_INFO.tsv` | `medication_administration` | `ORDER_MED_ID:LINE` | `ehi_mar_<id>_<line>` | Includes morphine equivalents |
| `IP_FLWSHT_MEAS.tsv` | `clinical_observation` | `FSD_ID:LINE` | `ehi_vitals_<id>_<line>` | No numeric values in export |
| `ORDER_MED.tsv` | `medication_request` | `ORDER_MED_ID` | `ehi_medorder_<id>` | — |
| `ORDER_PROC.tsv` | `service_request` | `ORDER_PROC_ID` | `ehi_procorder_<id>` | — |
| `IMM_ADMIN.tsv` | `immunization` | `DOCUMENT_ID` | `ehi_imm_<id>` | — |

**Excluded:** `Media/` directory (PDFs, images, audio — no BLOB storage), `CLARITY_EDG` (drug dictionary).

---

## Cross-Database Relationships

```
myhealth_auth                   myhealth_anthem                 myhealth_ucla
├── oauth_tokens (PRIMARY)      ├── oauth_tokens (REPLICA)      ├── oauth_tokens (REPLICA)
├── patients (PRIMARY)          ├── patients (REPLICA)          ├── patients (REPLICA)
├── entity_names (ORIGINAL)     ├── entity_names (REPLICA)      ├── entity_names (REPLICA)
└── pkce_verifiers              │                               │
    │                           │── eob_claims view              │── lab_results view
    │                           │   ├── eob                      │   ├── encounter (anchor)
    │                           │   │   ├── eob_item             │   │   ├── encounter_participant
    │                           │   │   │   └── eob_item_adj     │   │   ├── diagnostic_report
    │                           │   │   ├── eob_diagnosis        │   │   │   └── lab_result
    │                           │   │   ├── eob_care_team        │   │   ├── clinical_observation
    │                           │   │   └── eob_total            │   │   ├── clinical_note
    │                           │── claim_submission             │   │   ├── condition
    │                           │   ├── claim_item               │   │   ├── procedure_record
    │                           │   ├── claim_diagnosis          │   │   ├── medication_request
    │                           │   └── claim_care_team          │   │   ├── medication_administration
    │                           │── member_claim                 │   │   ├── service_request
    │                           │   └── member_claim_item        │   │   ├── specimen
    │                           │── member_claim_submission      │   │   ├── communication
    │                           │── eob (submission_origin,      │   │   ├── care_team
    │                           │    is_out_of_network)          │   │   ├── care_plan
    ├── job_run                 │── member_claims view           │   │   ├── document_reference
    │                           │── member_claims_recon          │   │   ├── allergy_intolerance
    └── _sync on exchange       │── member_claims_summary        │   │   ├── immunization
                                │                                │   │   ├── family_member_history
                                └── eob_claims → split_part()    │   │   └── medication_statement
                                    → patient_id from ref         │── clinical_overview view
```

### Key Cross-Table Links

- `eob.patient_ref` → `entity_names.entity_ref` — resolves patient name
- `eob.provider_ref` → `entity_names.entity_ref` — resolves provider name
- `eob.payee_ref` → `entity_names.entity_ref` — resolves payee name
- `oauth_tokens.patient_id` = `patients.patient_id` — tokens and patients linked by composite PK
- `split_part(eob.patient_ref, ':', 3)` = `oauth_tokens.patient_id` — views extract patient ID from ref
- `member_claim_item.cpt_code` ≈ `eob_item.hcpcs_code` — recon joins on code + date
- `member_claim_submission.matched_eob_id` → `eob.id` — portal submission matching
- `member_claim_item.claim_id` → `member_claim.id` — paper claim line items
- `diagnostic_report.encounter_id` → `encounter.id` — lab panel to visit
- `lab_result.report_id` → `diagnostic_report.id` — test to panel
- `*_encounter_id` → `encounter.id` — all clinical data links to visit anchor

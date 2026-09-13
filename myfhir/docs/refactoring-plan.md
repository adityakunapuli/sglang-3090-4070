# myhealth_fhir — Restructuring & Refactoring Plan

**Status:** proposed, awaiting go-ahead **Date:** 2026-09-13 **Scope:** `backend/src/myhealth_fhir` (28 files, ~15.0k
LOC) + `backend/pyproject.toml`
**Note on location:** this doc lives in `docs/` (tracked) rather than `.archive/` because
`.archive/` is gitignored — a living plan should survive fresh clones and be greppable in-repo.

---

## 1. Purpose

Eliminate the structural debt accumulated in the backend: dead model files, models living in
`db/` while a service lives in `models/`, a 1.6k-line `db/__init__.py` that runs hand-rolled schema migrations on every
process start, a 3.4k-line God module in `services/fhir_client.py`, 2.8k-line `cli.py`, and 156 function-level imports
papering over a tangled dependency graph.

**Explicit non-goals:** no database table/column renames, no changes to what data is fetched or stored, no dependency
swaps, no frontend changes. Every behavioral fix is called out separately (§9) and lands in its own commit.

---

## 2. Ground rules

1. **Each phase lands green.** After every commit: `uv run pytest -q`, `uv run ruff check src tests`, and the CLI/import
   smoke tests (Appendix D) pass.
2. **Pure moves are `git mv`.** Never mix a file move with content edits in the same commit — keeps `git log --follow`
   usable.
3. **Shims before rewrites.** When a hot module changes address (e.g. `services/fhir_client.py`), leave a re-export shim
   behind, migrate callers, delete the shim only after
   `grep` shows zero references and one full test cycle has passed.
4. **Behavior changes are isolated commits** (§9), never folded into moves.
5. **PHI discipline.** `services/report_generator.py` is gitignored for a reason (contains sensitive content). Never
   quote its body into docs, commit messages, or test fixtures.
6. **The running container keeps old code until rebuild.** All source moves are safe against the live daemon (it imports
   once at start); rebuild the image after each phase that moved files.

---

## 3. Current-state snapshot

### 3.1 Module inventory (LOC, excluding `__pycache__`)

| File                               |    LOC | Role                                                                        | Health                             |
|------------------------------------|-------:|-----------------------------------------------------------------------------|------------------------------------|
| `services/fhir_client.py`          |  3,382 | FHIRClient + 19 UCLA `save_*` fns + 2 anthem save methods + parsing helpers | God module                         |
| `cli.py`                           |  2,812 | 60+ click commands + ~800 ln of `print_*` helpers                           | God module                         |
| `db/__init__.py`                   |  1,638 | engines + sessions + migrations + views + backfills + replica sync          | God module, **code in `__init__`** |
| `db/models_ucla.py`                |    937 | 36 UCLA ORM models                                                          | OK, wrong dir                      |
| `services/report_generator.py`     |    893 | DOCX/HTML report generation                                                 | **gitignored inside package**      |
| `db/parser.py`                     |    736 | Anthem EOB/claim FHIR→row parsing                                           | OK                                 |
| `db/ucla_unpack.py`                |    654 | UCLA FHIR→row extractors                                                    | OK                                 |
| `services/ehi_importer.py`         |    518 | Epic EHI export importer                                                    | OK                                 |
| `db/models_anthem.py`              |    452 | Anthem ORM models                                                           | OK, wrong dir                      |
| `db/models.py`                     |    440 | Old single-DB models                                                        | **DEAD — zero imports**            |
| `services/auth.py`                 |    394 | AuthManager + RefreshDaemon                                                 | OK                                 |
| `job/__init__.py`                  |    370 | Job runner/loop/daemon                                                      | **code in `__init__`**             |
| `models/oauth.py`                  |    349 | OAuthToken + TokenStore                                                     | Service in `models/`               |
| `api/dashboard.py`                 |    282 | FastAPI routers                                                             | OK                                 |
| `api/claims.py`                    |    264 | Claim registration API                                                      | OK                                 |
| `services/member_submissions.py`   |    153 | Claim↔EOB matching                                                          | OK                                 |
| `config/settings.py`               |    149 | Provider registry                                                           | OK                                 |
| `db/rawjson_backfill.py`           |    131 | Raw-JSON backfill utility                                                   | OK                                 |
| `services/progress.py`             |    104 | Sync progress registry                                                      | OK                                 |
| `db/models_auth.py`                |     95 | Auth ORM models                                                             | OK, wrong dir                      |
| `db/identity.py`                   |     94 | entity_ref helpers + registry upserts                                       | OK                                 |
| `main.py`                          |     77 | FastAPI app                                                                 | OK                                 |
| `services/validate_docx_layout.py` |     62 | DOCX validation                                                             | OK                                 |
| 4× docstring-only `__init__.py`    | 1 each | —                                                                           | Noise                              |

### 3.2 Key metrics

| Metric                                         |                                                                                                                                                                                                     Value |
|------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------:|
| Total `.py` LOC                                |                                                                                                                                                                                                    14,996 |
| Function-level `from myhealth_fhir...` imports | **156** (fhir_client 88, cli 17, ehi_importer 14, models/oauth 13, services/auth 6, db/__init__ 4, rawjson_backfill 3, identity 3, ucla_unpack 2, api/dashboard 2, main 1, job 1, parser 1, api/claims 1) |
| `from __future__ import annotations`           |                                                                                                                                                              **10 files** (py3.13 target ⇒ all removable) |
| `any` used as a type annotation                |                                                                                                                                                                                     **7 sites** (see P10) |
| Broad `except Exception:` handlers             |                                                                                                                                                                                                        27 |
| Test files                                     |                                                                                                                             4 (parser classification, ucla unpack, member submissions, anthem view edges) |
| Alembic migrations                             |                                                                                                                                                                       0 (hand-rolled startup DDL instead) |

### 3.3 Dependency map (as-is)

```
main.py ──► api/{dashboard,claims} ──► services/{auth,fhir_client,progress} ──┐
   │                       │                                                  │
   └──► job/__init__ ──────┘                                                  ▼
cli.py ──► everything                                 db/__init__ ──► db/models_{auth,anthem,ucla}
                                                          │
models/oauth.py ──► db (sessions, init_db, _sync_oauth_tokens_replica)
db/__init__.py  ──► db/models_*            (fine)
db/identity.py  ──► db/models_*            (lazy, per-DB dispatch by URL sniffing)
fhir_client.py  ──► db + models + parser + ucla_unpack + member_submissions (lazy ×88)
```

Layering violations:

- `models/oauth.py` (a **service**: TokenStore writes DB rows) imports `db` sessions and even a **private** function
  `_sync_oauth_tokens_replica` (`models/oauth.py:220`).
- `db/__init__.py` reaches up into model modules (fine directionally) but also owns cross-DB replication logic (not a
  `db` concern).
- `fhir_client.py` does DB persistence (should be below the service layer or its own layer).
- `api/dashboard.py:130` imports private `_COUNTERS` from `job/__init__.py`.
- `tests/test_anthem_view_edges.py:18` imports private `_create_anthem_views`.
- `fhir_client.py` lazily imports **itself** three times (`:659, :891, :922`) — symptom of the methods/fns split that
  never happened.

---

## 4. Problem catalog

| #   | Problem                                                                                                                                                                                                                                                                                             | Evidence                            |
|-----|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------|
| P1  | `db/models.py` is dead code — superseded by `models_anthem/ucla/auth`, zero imports anywhere                                                                                                                                                                                                        | grep across `src` + `tests`         |
| P2  | ORM models live in `db/`; `models/` holds a DB-writing service                                                                                                                                                                                                                                      | `db/models_*.py`, `models/oauth.py` |
| P3  | `db/__init__.py` = engines + sessions + migrations + views + backfills + OAuth replica sync (1,638 ln)                                                                                                                                                                                              | file itself                         |
| P4  | Schema "migrations" run on **every process start** (every CLI invocation): `create_all`, `DROP VIEW ... CASCADE`, ALTER/backfills, `_validate_identity_backfill` raising `RuntimeError` at boot                                                                                                     | `db/__init__.py:274–321`            |
| P5  | `job/__init__.py` is 370 ln of the job runner                                                                                                                                                                                                                                                       | file itself                         |
| P6  | `fhir_client.py` = HTTP client + OAuth glue + pagination/checkpoints + parsing + persistence + progress reporting                                                                                                                                                                                   | file itself                         |
| P7  | 156 lazy imports; circular untangling by defer                                                                                                                                                                                                                                                      | Appendix B                          |
| P8  | `cli.py` 2,812 ln, monolithic                                                                                                                                                                                                                                                                       | file itself                         |
| P9  | Duplicated extraction layers & helpers: `ucla_unpack` extractors vs inline parsing inside `save_*`; `_as_list`/`_first`/`_parse_dt`/coding-ref helpers ×4 modules; observation-value parsing ×3                                                                                                     | Appendix C                          |
| P10 | `__future__` imports (10 files) + ruff ban on module-level `typing` imports forces `any`-as-annotation hacks: `fhir_client.py:500,649,771,1115` (`on_auth_failure: any`), `cli.py:2567` (`fetch_client: any`), `models/oauth.py:117` (`record: any`), `db/__init__.py` (`_engines: dict[str, any]`) | grep                                |
| P11 | `save_*` return counts only INSERTs, not UPDATEs → job-run summaries understate                                                                                                                                                                                                                     | e.g. `fhir_client.py:2337–2388`     |
| P12 | 27 broad `except Exception:`, several silent `pass`                                                                                                                                                                                                                                                 | grep                                |
| P13 | Private cross-module imports (`_COUNTERS`, `_create_anthem_views`, `_sync_oauth_tokens_replica`)                                                                                                                                                                                                    | §3.3                                |
| P14 | Root `__init__.py` imports `.cli` and `.main` → importing the package pulls FastAPI+Click+everything; nothing imports root attrs                                                                                                                                                                    | `__init__.py:1–6`                   |
| P15 | Docstring-only `__init__.py` files (api, config, models, services)                                                                                                                                                                                                                                  | PEP 420: removable                  |
| P16 | Stale `__pycache__` for py3.12/3.13/3.14 in tree (untracked, noise; hazard under the volume-mounted container)                                                                                                                                                                                      | `find src -name __pycache__`        |
| P17 | `report_generator.py` gitignored but inside the package: a fresh-clone Docker build (`uv sync --frozen` + `COPY src/`) produces an image without it; lazy import at `cli.py:1620` masks it until `myhealth ucla-report` runs                                                                        | `.gitignore`, `Dockerfile`          |

Dead backward-compat machinery discovered in `db/__init__.py` (beyond P1): `provider_context`,
`_provider_context`, `_resolve_provider`, `get_engine()`, `get_session()`,
`get_clinical_session()` — **no importers** anywhere. Only `get_session_for`, the three
`get_*_session`, three `get_*_engine`, `init_db`, `is_postgres` (internal) are live.

---

## 5. Target architecture

### 5.1 Tree

```
src/myhealth_fhir/
├── __init__.py              # __version__ only — no imports
├── main.py                  # FastAPI app (unchanged)
├── cli/
│   ├── __init__.py          # click group assembly; exposes main, anthem_main (composition root)
│   ├── main.py              # root group: job, server, db, mcd, formulary, anthem, ucla
│   ├── anthem.py            # anthem/eob/claims/coverage/patients/submission/update
│   ├── ucla.py              # ucla_* commands (patients, save-labs, save-all, ehi, report)
│   ├── auth.py              # make_auth_group + do_login/status/refresh/clear/daemon
│   ├── search.py            # search_claims, search_eob
│   └── output.py            # print_* + _get_observation_value_str etc.
├── db/
│   ├── __init__.py          # docstring only (shim deleted Phase 6a)
│   ├── engine.py            # URL resolution, engines, sessions, PROVIDER_DB, get_session_for
│   └── schema/
│       ├── bootstrap.py     # init_db / _init_db_once (thin until Alembic)
│       ├── migrations.py    # _migrate_*, _backfill_*, _validate_identity_backfill
│       └── views.py         # _create_anthem_views, _create_ucla_views
├── models/                  # ALL ORM models
│   ├── __init__.py          # re-exports every public model class
│   ├── auth.py              # ← db/models_auth.py
│   ├── anthem.py            # ← db/models_anthem.py
│   └── ucla.py              # ← db/models_ucla.py
├── fhir/
│   ├── client.py            # FHIRClient: HTTP, auth glue, pagination, checkpoints
│   ├── parsing.py           # shared FHIR-dict helpers (single source; absorbs parser.py/ucla_unpack helpers)
│   ├── anthem_save.py       # save_eobs_to_db, save_claims_to_db (converted from methods)
│   ├── ucla_save.py         # the 19 save_*_to_db functions
│   └── notes.py             # save_clinical_notes_from_docs, backfill_clinical_note_attachments
├── services/
│   ├── oauth.py             # ← models/oauth.py (OAuthToken, TokenStore)
│   ├── oauth_sync.py        # ← _sync_oauth_tokens_replica (from db/__init__)
│   ├── auth.py              # AuthManager, RefreshDaemon (unchanged)
│   ├── progress.py, member_submissions.py, ehi_importer.py
│   ├── report_generator.py  # path unchanged (gitignored — see P17)
│   └── validate_docx_layout.py
├── job/
│   ├── __init__.py          # docstring only
│   └── runner.py            # ← job/__init__.py (COUNTERS made public)
└── api/                     # dashboard.py, claims.py (unchanged)
```

### 5.2 Layering rules (enforced by review; optionally by `ruff` TID + a custom check)

```
models        ← imports nothing from db/services/fhir/cli
db.engine     ← imports models only
db.schema     ← imports db.engine + models
fhir          ← imports db.engine, models, fhir.parsing     (never services)
services      ← imports db.engine, models, fhir            (never cli/api)
cli, api      ← anything
```

Once this holds, ~all 156 lazy imports become module-level imports.

---

## 6. Module migration map

| Old                                                                                               | New                                         | Notes                                                        |
|---------------------------------------------------------------------------------------------------|---------------------------------------------|--------------------------------------------------------------|
| `db/models.py`                                                                                    | *(deleted)*                                 | dead code                                                    |
| `db/models_auth.py`                                                                               | `models/auth.py`                            | `git mv`                                                     |
| `db/models_anthem.py`                                                                             | `models/anthem.py`                          | `git mv`                                                     |
| `db/models_ucla.py`                                                                               | `models/ucla.py`                            | `git mv`                                                     |
| `models/oauth.py`                                                                                 | `services/oauth.py`                         | `git mv`; service, not a model                               |
| `db/__init__.py` engines/sessions                                                                 | `db/engine.py`                              | includes `PROVIDER_DB`, `get_session_for`                    |
| `db/__init__.py` init/migrations/views                                                            | `db/schema/{bootstrap,migrations,views}.py` |                                                              |
| `db/__init__.py` `_sync_oauth_tokens_replica`                                                     | `services/oauth_sync.py`                    | cross-DB writer ≠ db infra                                   |
| `db/__init__.py` compat (`provider_context`, `get_session`, `get_engine`, `get_clinical_session`) | *(deleted)*                                 | zero importers                                               |
| `services/fhir_client.py` FHIRClient                                                              | `fhir/client.py`                            | fetch/checkpoint logic stays                                 |
| `services/fhir_client.py` save methods                                                            | `fhir/anthem_save.py`                       | `save_eobs_to_db(client, eobs)`                              |
| `services/fhir_client.py` 19 `save_*`                                                             | `fhir/ucla_save.py` (+ `fhir/notes.py`)     |                                                              |
| `services/fhir_client.py` helpers                                                                 | `fhir/parsing.py`                           | merge with `db/parser.py`+`db/ucla_unpack.py` shared helpers |
| `cli.py`                                                                                          | `cli/` package                              | split by domain                                              |
| `job/__init__.py`                                                                                 | `job/runner.py`                             | `_COUNTERS` → `COUNTERS`                                     |
| `config/` package                                                                                 | `config.py`                                 | drop empty init                                              |
| Import alias                                                                                      |                                             |                                                              |
| `myhealth_fhir.db.models_anthem`                                                                  | `myhealth_fhir.models.anthem`               | sed, 7 files                                                 |
| `myhealth_fhir.db.models_ucla`                                                                    | `myhealth_fhir.models.ucla`                 | sed, 5 files                                                 |
| `myhealth_fhir.db.models_auth`                                                                    | `myhealth_fhir.models.auth`                 | sed, 6 files                                                 |
| `myhealth_fhir.models.oauth`                                                                      | `myhealth_fhir.services.oauth`              | 1 file (`services/auth.py`)                                  |
| `myhealth_fhir.db` (sessions/engines)                                                             | `myhealth_fhir.db.engine`                   | ~17 files via shim first                                     |
| `myhealth_fhir.db.init_db`                                                                        | `myhealth_fhir.db.schema.bootstrap`         | main, cli, fhir_client, services/oauth                       |
| `myhealth_fhir.services.fhir_client`                                                              | `myhealth_fhir.fhir.client`                 | 6 files via shim first                                       |
| `myhealth_fhir.job`                                                                               | `myhealth_fhir.job.runner`                  | 4 files (main, cli, api/dashboard ×2)                        |

---

## 7. Phased execution plan

### Phase 0 — Baseline & safety net *(~30 min, risk: none)*

**Objective:** capture the regression fence before touching anything.

1. `git switch -c refactor/backend && git switch -c refactor/phase-0` (one branch per phase off a shared
   `refactor/backend`).
2. Run and save: `uv run pytest -q | tee /tmp/baseline-pytest.txt`,
   `uv run ruff check src tests | tee /tmp/baseline-ruff.txt`.
3. Confirm how tests get a DB: `tests/test_anthem_view_edges.py` uses `get_anthem_engine()` — verify it passes both
   against live Postgres and the SQLite dev fallback (`_resolve_db_url` falls back to `data/*.db`). Record which mode is
   the norm.
4. Optional: `ha`-style snapshot is N/A here — instead `pg_dump` the three databases if Postgres is running
   (`pg_dump myhealth_anthem > /tmp/anthem.sql` etc.). Cheap insurance before Phase 2/6.

**Done when:** baseline files exist; tests green in the recorded mode.

---

### Phase 1 — Mechanical hygiene *(~1–2 h, risk: low)*

**Objective:** dead code out, `__future__` out, `any` annotations fixed, `__init__` noise gone. No import-path changes
at all.

1. Delete `db/models.py` (P1).
2. Delete dead compat machinery from `db/__init__.py` (§4 list): `provider_context`, `_provider_context`,
   `_resolve_provider`, `get_engine`, `get_session`, `get_clinical_session`. Keep `get_session_for`.
3. Strip `from __future__ import annotations` from all 10 files:
   `grep -rl "from __future__ import annotations" src | xargs sed -i '/from __future__ import annotations/d'`
   **Safety note:** verified there are no self-referencing annotations inside class bodies (the only `OAuthToken`
   self-refs are in `TokenStore`, defined after it). SQLAlchemy
   `Mapped[...]` resolves eagerly fine on 3.13. Run tests immediately after.
4. `pyproject.toml`: remove `"typing"` from `[tool.ruff.lint.flake8-tidy-imports] banned-module-level-imports`; delete
   the stale TC comment. (Keep `ban-relative-imports = "all"`.)
5. Fix the 7 `any`-as-annotation sites → `typing.Any` (add `from typing import Any`):
   `fhir_client.py:500,649,771,1115`, `cli.py:2567`, `models/oauth.py:117`,
   `db/__init__.py` `_engines`. Then re-grep: `grep -rEn ": any\b|-> any\b|,\s*any\b|\[any\]" src` → 0.
6. Delete the four docstring-only inits (`api/`, `config/`, `models/`, `services/`). **Keep root `__init__.py`** as a
   regular package marker but slim it to `__version__ = "0.2.0"`
   (P14) — nothing imports `myhealth_fhir.app`/`.main` (verified).
7. `find src -name __pycache__ -type d -prune -exec rm -rf {} +` (P16).
8. Dependency audit (quick): `grep -rn "import fhirclient\|from fhirclient" src` — if unused alongside `fhirpy`, note
   for removal in a later commit (don't mix into this one).

**Verification:** pytest, ruff, `uv run python -c "import myhealth_fhir.main, myhealth_fhir.cli"`,
`uv run myhealth --help`. **Rollback:** revert commit.

---

### Phase 2 — Split `db/__init__.py`  *(~3–4 h, risk: medium)*

**Objective:** `db/__init__.py` shrinks to a re-export shim; every concern gets a real home.

1. `git mv` nothing here — create `db/engine.py` with: `_project_root`, `_load_project_env`,
   `_resolve_db_url`, `_create_engine` + engine cache, `get_{anthem,ucla,auth}_engine`,
   `PROVIDER_DB`, `get_{anthem,ucla,auth}_session`, `get_session_for`, `is_postgres`.
2. Create `db/schema/bootstrap.py`: `init_db`, `_init_db_once`, `_initialized`, `_init_lock`.
3. Create `db/schema/migrations.py`: all `_migrate_*`, `_backfill_*`,
   `_validate_identity_backfill` (they import `db.engine` + `models` — direction is now clean).
4. Create `db/schema/views.py`: `_create_anthem_views`, `_create_ucla_views`.
5. Create `services/oauth_sync.py`: `sync_oauth_tokens_replica()` (renamed from
   `_sync_oauth_tokens_replica`); update `models/oauth.py:220` import.
6. `db/__init__.py` becomes an explicit re-export shim (no logic):
   ```python
   from myhealth_fhir.db.engine import (
       PROVIDER_DB, get_anthem_engine, get_auth_engine, get_ucla_engine,
       get_anthem_session, get_auth_session, get_ucla_session, get_session_for,
   )
   from myhealth_fhir.db.schema.bootstrap import init_db
   from myhealth_fhir.db.schema.views import _create_anthem_views, _create_ucla_views  # shim only
   ```
7. Update `tests/test_anthem_view_edges.py` to import from the new homes (kills a P13 item).
8. Do **not** change the ~17 `from myhealth_fhir.db import ...` callers yet — the shim absorbs them; callers migrate in
   Phase 6/7 cleanup.

**Verification:** pytest; `uv run myhealth db backfill-raw-json --dry-run` (exercises engine+migrations); grep: no logic
left in `db/__init__.py` besides imports/`__all__`. **Rollback:** revert (no DB writes changed in this phase — code only
moved).

---

### Phase 3 — Consolidate models *(~2 h, risk: medium-low)*

**Objective:** all ORM models under `models/`, service out of `models/`.

1. `git mv src/myhealth_fhir/db/models_auth.py src/myhealth_fhir/models/auth.py` (repeat for anthem/ucla). Move
   **verbatim**.
2. Write `models/__init__.py` re-exporting every public ORM class from the three modules (copy the name lists from the
   old `db/__init__.py` import blocks — that's the known-good set).
3. Mechanical rewrite across `src` + `tests`:
   ```bash
   grep -rl "myhealth_fhir.db.models_anthem" src tests | xargs sed -i 's/myhealth_fhir\.db\.models_anthem/myhealth_fhir.models.anthem/g'
   grep -rl "myhealth_fhir.db.models_ucla"  src tests | xargs sed -i 's/myhealth_fhir\.db\.models_ucla/myhealth_fhir.models.ucla/g'
   grep -rl "myhealth_fhir.db.models_auth"  src tests | xargs sed -i 's/myhealth_fhir\.db\.models_auth/myhealth_fhir.models.auth/g'
   ```
4. `git mv models/oauth.py services/oauth.py`; update `services/auth.py:17`
   (`from myhealth_fhir.models.oauth import …` → `from myhealth_fhir.services.oauth import …`).
5. Grep to zero: `grep -rn "models_anthem\|models_ucla\|models_auth\|models\.oauth" src tests`.

**Verification:** pytest; `uv run myhealth anthem auth status` + `uv run myhealth ucla patients --count 1` (touches
auth/ucla model paths end-to-end; needs tokens/DB). **Rollback:** revert commit (pure moves).

---

### Phase 4 — Dismember `fhir_client.py`  *(~1 day, risk: medium-high — do in 4 sub-commits)*

**Objective:** HTTP client stops being a persistence layer. **Pure moves first, dedupe second.**

4a. Extract `fhir/parsing.py`: `_clean_error_message`, `_max_last_updated`, `_parse_dt`,
`_parse_loinc`, `_parse_code_display`, `_fh_error_text`, `_extract_observation_value`,
`_extract_patient_id`, `_extract_encounter_id`, `_existing_encounter_id`, `_coding_first_*`,
`_as_list`, `_identifier_rows`, `_extension_value`, `_component_rows`, `client_ref`. Then fold in the equivalent helpers
from `db/parser.py` (`_as_list`, `_first`, `parse_date`,
`coding`, `ref`) and `db/ucla_unpack.py` (`_as_list`, `_first`, `_dt`, `_cc`, `_cd`, `_raw_ref`)
so there is **one** implementation each (P9). Keep the `extract_*`/`load_*` domain functions where they are for now. 4b.
`fhir/anthem_save.py`: convert `FHIRClient.save_eobs_to_db` / `save_claims_to_db` to module functions
`save_eobs_to_db(client, eobs)` / `save_claims_to_db(client, claims)`. Callers inside the class become one-line
delegations (or direct imports — pick per call site). 4c. `fhir/ucla_save.py`: `git`-move the 19 `save_*_to_db`
functions verbatim.
`fhir/notes.py`: `save_clinical_notes_from_docs`, `backfill_clinical_note_attachments`. 4d. `fhir/client.py`:
FHIRClient + `get_fhir_client` + fetch/pagination/checkpoint/stage-reporting. It imports `fhir.anthem_save` /
`fhir.ucla_save` / `fhir.notes` for the persist steps.

5. Leave `services/fhir_client.py` as a shim re-exporting `FHIRClient`, `get_fhir_client`,
   `backfill_clinical_note_attachments`, and all `save_*` names; then migrate its 6 importers (`job/runner`,
   `api/dashboard`, `cli.py`, internal self-imports) and delete the shim in the same phase's final commit once grep is
   clean.
6. While touching `fhir/`, also dedupe the inline re-parsing inside `save_medication_statements_to_db`
   et al. against the `ucla_unpack` extractors **only where trivial** (the extractor is already called; delete the
   duplicated inline field scraping). Anything non-trivial: leave for a follow-up issue rather than expand this phase.

**Verification:** pytest; grep `save_.*_to_db` importers all point at `myhealth_fhir.fhir.*`;
`uv run python -c "from myhealth_fhir.fhir.client import FHIRClient"`. **Rollback:** revert per sub-commit.

---

### Phase 5 — Split CLI + `job/runner.py`  *(~half day, risk: medium-low)*

1. `cli/` package per §5.1. `git mv cli.py cli/main.py`, then split: `output.py` gets the
   `print_*`/`_get_*` helpers (~800 ln), `anthem.py`, `ucla.py`, `auth.py`, `search.py`,
   `submissions.py` get their commands. Register subgroups on the root group in `cli/__init__.py`.
2. Entry points: pyproject has `myhealth = "myhealth_fhir.cli:main"` and
   `anthem = "myhealth_fhir.cli:anthem_main"` — keep those exact attribute paths working from
   `cli/__init__.py`.
3. `git mv job/__init__.py job/runner.py`; rename `_COUNTERS` → `COUNTERS`; update the 4 importers (`main.py`,
   `cli.py:1679`, `api/dashboard.py:130,221`) — kills another P13 item. Drop in a one-line `job/__init__.py` re-export
   shim, or skip the shim (only 4 sites) — prefer skipping here since importers are few and known.

**Verification:** `uv run myhealth --help`, `myhealth anthem --help`, `myhealth ucla --help`,
`myhealth submission --help`, `myhealth job --dry...` (or `--help` at minimum);
`uv run pytest -q`.

---

### Phase 6 — Import-path cleanup + Alembic adoption *(Alembic: 1–2 days, risk: high; cleanup: 1 h)*

6a. **Cleanup (cheap, do regardless):** migrate the remaining `from myhealth_fhir.db import …`
callers to `myhealth_fhir.db.engine` / `db.schema.bootstrap`; then reduce `db/__init__.py`
to nothing (delete the shim once grep is zero) or keep a 5-line re-export if you prefer the short form — decide once,
document in AGENTS.md. 6b. **Alembic (recommended, can be deferred):**

- One Alembic environment per database (`-n auth`, `-n anthem`, `-n ucla`), each with its own
  `alembic_version` table in its own DB. - **Baseline by stamping, not generating:** autogenerate a baseline revision
  from the current models, then `alembic stamp head` against the three live DBs so history starts at "current". Never
  run the baseline against live data as a migration. - `init_db()` in `db/schema/bootstrap.py` shrinks to: create
  engines → run
  `alembic upgrade head`
  per DB (programmatic API) → done. View DDL becomes an Alembic migration (views are cheap to
  `CREATE OR REPLACE`). - Add a pre-flight: refuse to run if `alembic_version` is missing in a non-empty DB (guards the
  "stamping forgotten on a fresh environment" failure mode). - Before first live run: `pg_dump` all three DBs (Phase 0
  habit).

**Verification:** against a **copied** database first: drop/recreate schema in the copy, run
`alembic upgrade head` from empty, run `bootstrap`, diff `pg_dump --schema-only` before/after vs the live DBs' current
shape.

---

### Phase 7 — Hardening *(ongoing, risk: low)*

1. Ruff: add `"B"` (bugbear), `"RET"`, `"SIM"`; fix findings. Audit the 27 broad
   `except Exception:` (list via Appendix D grep) — each gets narrowed, logged, or an explicit
   `# noqa: BLE001` with a reason.
2. Tests to add (all runnable on SQLite fallback):
    - `TokenStore` save/load/`migrate_legacy` round-trip,
    - `AuthManager.parse_token_response` (fixture bodies incl. error shapes),
    - one `save_*` upsert round-trip (insert → update → assert fields + raw_json),
    - `api/dashboard` status endpoint via `fastapi.testclient` with engines pointed at SQLite tmp,
    - golden-ish tests for `fhir/parsing.py` helpers.
3. Remove any remaining shims (`db/__init__.py`, `services/fhir_client.py`) after zero-reference grep + one CI cycle.
4. Update `AGENTS.md` architecture tree + add the layering rules from §5.2; note `docs/schema.md`
   is unaffected (table names unchanged).
5. Decide P17: either commit a redacted `report_generator.py`, move the sensitive template data out of the module into a
   gitignored data file the module reads at runtime, or add a friendly runtime error when the module is missing. (User
   decision — touches PHI policy.)

---

## 8. Import rewrite mechanics (copy-paste)

```bash
cd /mnt/data/docker/myfhir/backend
# Phase 3 model moves
grep -rl "myhealth_fhir.db.models_anthem" src tests | xargs sed -i 's/myhealth_fhir\.db\.models_anthem/myhealth_fhir.models.anthem/g'
grep -rl "myhealth_fhir.db.models_ucla"  src tests | xargs sed -i 's/myhealth_fhir\.db\.models_ucla/myhealth_fhir.models.ucla/g'
grep -rl "myhealth_fhir.db.models_auth"  src tests | xargs sed -i 's/myhealth_fhir\.db\.models_auth/myhealth_fhir.models.auth/g'
# Phase 6a session/init migration
grep -rl "from myhealth_fhir.db import init_db" src | xargs sed -i 's/from myhealth_fhir.db import init_db/from myhealth_fhir.db.schema.bootstrap import init_db/'
# always finish with:
uv run ruff check --fix src tests && uv run ruff format src tests
```

After every rewrite: `uv run python -c "import myhealth_fhir.main, myhealth_fhir.cli"`.

---

## 9. Behavior fixes & bug list (each its own commit, never inside a move)

| #  | Fix                                                                                                       | Where                                                                                    | Notes                                 |
|----|-----------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------|---------------------------------------|
| B1 | `save_*` should count UPDATEs, not just INSERTs (or return `(inserted, updated)`)                         | `fhir/ucla_save.py` (all 19)                                                             | check `job` summary consumers first   |
| B2 | Exception-handling audit (27 sites)                                                                       | everywhere                                                                               | narrow / log / explicit noqa          |
| B3 | `report_generator.py` missing from fresh-clone Docker builds                                              | `.gitignore` + `cli.py:1620`                                                             | user decision (PHI) — see Phase 7.5   |
| B4 | `any` → `Any` (7 sites)                                                                                   | Phase 1.5                                                                                | also enables fixing P10 at the root   |
| B5 | Unused `provider="ucla"` params on the 19 `save_*` fns — wire through `get_session_for(provider)` or drop | `fhir/ucla_save.py`                                                                      | decide before Phase 4c to avoid churn |
| B6 | Private cross-module imports                                                                              | `_COUNTERS`→`COUNTERS`, `_create_anthem_views` test import, `_sync_oauth_tokens_replica` | resolved inside Phases 2/5            |
| B7 | Retire `models/oauth.py` legacy JSON token path once no `.auth/*/token.json` files remain                 | `services/oauth.py`                                                                      | confirm on disk first                 |
| B8 | Self-imports in `fhir_client.py:659,891,922`                                                              | disappears with Phase 4                                                                  |                                       |

---

## 10. Risk register

| Risk                                                                         | Mitigation                                                                                                                            |
|------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------|
| Eager annotations break a hidden forward reference                           | grep evidence already done; run pytest + all three CLI smoke imports immediately after Phase 1.3; `python -X dev` to surface warnings |
| Large-move regressions in `fhir_client.py`                                   | pure `git`-moves verbatim; shim for 6 importers; per-sub-commit rollback                                                              |
| Stale `__pycache__` under the volume-mounted container shadowing new modules | `find src -name __pycache__ -type d -prune -exec rm -rf {} +` each phase; rebuild image after moves                                   |
| Alembic stamping mistakes on live DBs                                        | Phase 0 `pg_dump`; practice on schema-copy DB; pre-flight guard; never autogenerate-apply baseline to live                            |
| PHI leakage via moved/gitignored files                                       | never quote `report_generator.py`; keep its path stable; `.archive/` stays ignored                                                    |
| Live daemon confused mid-refactor                                            | container runs old code until rebuild — rebuild per phase; nothing writes schema between rebuilds                                     |
| History noise from moves+edits                                               | strict rule 2 (§2): moves are verbatim, separate commits                                                                              |
| Divergent duplicate parsers reintroduced later                               | §5.2 layering rules into AGENTS.md; ruff `B`/`SIM` + review checklist                                                                 |

---

## 11. Final acceptance checklist

- [ ] `db/models.py`, dead compat session helpers, docstring-only inits: gone
- [ ] `grep -rn "from __future__" src` → 0
- [ ] `grep -rEn ": any\b|-> any\b|\[any\]" src` → 0
- [ ] All ORM models importable from `myhealth_fhir.models`; `grep -rn "models_anthem\|models_ucla\|models_auth" src` →
  0
- [ ] 
  `grep -rn "myhealth_fhir.db import\|myhealth_fhir.db\." src | grep -v "db.engine\|db.schema\|db.parser\|db.identity\|db.ucla_unpack\|db.rawjson_backfill"` →
  0 (or documented shim)
- [ ] No `save_*`/persistence code inside `fhir/client.py`; `services/fhir_client.py` deleted
- [ ] `db/__init__.py` ≤ 10 lines (or deleted); `job/runner.py` exists; `cli/` package replaces `cli.py`
- [ ] Function-level `myhealth_fhir` imports ≤ 20, each with a reason comment
- [ ] `pytest`, `ruff check`, CLI smoke tests green; AGENTS.md updated
- [ ] (Phase 6) Alembic baseline stamped on all three live DBs; `init_db` runs `upgrade head`

---

## Appendix A — Dead code inventory (delete in Phase 1)

- `db/models.py` — entire file (440 ln, zero importers)
- `db/__init__.py`: `provider_context`, `_provider_context`, `_resolve_provider`,
  `get_engine()`, `get_session()`, `get_clinical_session()`
- root `__init__.py`: the `from .cli import main; from .main import app` exports (nothing imports them)
- candidate (B7): `services/oauth.py` legacy JSON token path (`legacy_token_path`,
  `TokenStore.migrate_legacy`, `OAuthToken.to_dict/from_dict`) once no `.auth/*/token.json` files exist

## Appendix B — Lazy-import inventory summary (Phase 4/6/7 targets)

| File                       | Count | Dominant pattern                                                  |
|----------------------------|------:|-------------------------------------------------------------------|
| `services/fhir_client.py`  |    88 | persistence fns importing db/models/extractors; self-imports      |
| `cli.py`                   |    17 | deferred heavy deps (ehi_importer, report_generator, db sessions) |
| `services/ehi_importer.py` |    14 | db sessions + models inside methods                               |
| `models/oauth.py`          |    13 | db sessions + models_auth rows                                    |
| `services/auth.py`         |     6 | PKCEVerifier + sessions                                           |
| others (9 files)           |    18 | scattered                                                         |

Post-refactor policy: module-level imports everywhere; documented exceptions only for genuinely heavy/optional deps
inside rarely-executed paths (e.g. `python-docx`, `markitdown`, `fhirpy`
behind CLI-only commands). Target ≤ 20 with reasons.

## Appendix C — Duplication inventory (Phase 4a merge targets)

| Helper                                           | Sites                                                                                                                       |
|--------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------|
| `_as_list`                                       | `db/parser.py:88`, `db/ucla_unpack.py:28`, `fhir_client.py:2050`                                                            |
| `_first`                                         | `db/parser.py:97`, `db/ucla_unpack.py:34`                                                                                   |
| datetime parsing                                 | `fhir_client.py:1485 _parse_dt`, `ehi_importer.py:67 _parse_dt`, `ucla_unpack.py:39 _dt`, `parser.py:25 parse_date`         |
| coding/ref extraction                            | `parser.py:32–71`, `ucla_unpack.py:63–91`, `fhir_client.py:1503–1522, 2034–2049`                                            |
| observation value parsing                        | `fhir_client.py:1542 _extract_observation_value`, `ucla_unpack.py:157 _obs_value`, `cli.py:2440 _get_observation_value_str` |
| identifier rows                                  | `parser.py:103 identifier_rows`, `fhir_client.py:2057 _identifier_rows`                                                     |
| RTF→text                                         | `report_generator.py:47 _rtf_to_text`, `ehi_importer.py:88 _rtf_to_text`                                                    |
| EntityName/PatientRecord/OAuthTokenRecord ×3 DBs | accepted cost; optional future: SQLAlchemy `declared_attr` mixins for shared columns                                        |

## Appendix D — Verification command sheet

```bash
cd /mnt/data/docker/myfhir/backend
uv run ruff check src tests
uv run pytest -q
uv run python -c "import myhealth_fhir.main, myhealth_fhir.cli"
uv run myhealth --help && uv run myhealth anthem --help && uv run myhealth ucla --help && uv run myhealth submission --help
find src -name __pycache__ -type d -prune -exec rm -rf {} +
# progress greps
grep -rn "from __future__" src | wc -l
grep -rEn ": any\b|-> any\b|\[any\]" src | wc -l
grep -rn "models_anthem\|models_ucla\|models_auth" src tests | wc -l
grep -rn "from myhealth_fhir.db import" src | wc -l
```

## Appendix E — Suggested commit series

1. `chore(db): remove dead db/models.py and unused compat session helpers`
2. `refactor: drop __future__ annotations; lift typing ban; replace any-annotations with Any`
3. `chore: slim package __init__ files; remove docstring-only inits; clean pycache`
4. `refactor(db): extract engine/session layer to db/engine.py`
5. `refactor(db): move schema bootstrap, migrations, views to db/schema/`
6. `refactor(services): extract OAuth token replica sync to services/oauth_sync.py`
7. `refactor(models): move db/models_{auth,anthem,ucla} to models/{auth,anthem,ucla}`
8. `refactor(services): move models/oauth.py to services/oauth.py`
9. `refactor(fhir): extract shared parsing helpers to fhir/parsing.py (dedupe parser/ucla_unpack)`
10. `refactor(fhir): extract anthem persistence to fhir/anthem_save.py`
11. `refactor(fhir): extract UCLA save_* to fhir/ucla_save.py + fhir/notes.py`
12. `refactor(fhir): slim FHIRClient to fetch/pagination; retire services/fhir_client shim`
13. `fix(fhir): count updated rows in save_* return values` (B1 — after consumer check)
14. `refactor(cli): split cli.py into cli/ package; move job/__init__.py to job/runner.py`
15. `fix: publicize COUNTERS and views helpers; remove private cross-module imports`
16. `build(db): adopt Alembic with per-database environments (stamp baseline)` *(Phase 6b, optional)*
17. `chore(lint): enable B/RET/SIM; resolve exception-handling audit`
18. `test: TokenStore/AuthManager/save round-trip/dashboard tests`

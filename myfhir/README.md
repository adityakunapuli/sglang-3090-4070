# MyHealth FHIR Dashboard

Multi-provider FHIR data-pull job with a web dashboard. Schedules background
fetches of insurance (Anthem/Elevance Health EOBs/claims) and clinical (Epic,
UCLA configured) data into the shared PostgreSQL instance, and exposes a
dashboard for last-run status, record totals, token health, and manual OAuth
re-authentication.

This is a scrubbed, public-ready build — PHI tests copied to
`backend/tests/` use anonymized identifiers.

## Layout

```
myfhir/
├── backend/            # Python (uv) package `myhealth_fhir`
│   ├── src/myhealth_fhir/
│   │   ├── cli.py             # Click CLI: anthem/*, ucla/*, job
│   │   ├── main.py            # FastAPI app (dashboard API + job daemon)
│   │   ├── api/dashboard.py   # /api/status, /api/auth/{provider}/*
│   │   ├── job/               # data-pull orchestrator + daemon thread
│   │   ├── config/settings.py # provider registry (anthem + epic)
│   │   ├── db/                # 3 databases, ORM models, session helpers
│   │   ├── models/oauth.py    # OAuthToken + TokenStore
│   │   └── services/          # auth (OAuth2/PKCE), fhir_client, ...
│   ├── tests/
│   ├── Dockerfile
│   └── pyproject.toml
├── frontend/           # Vite + React dashboard, nginx SPA + /api proxy
├── docs/               # Anthem/Epic API references + schema.md
├── docker-compose.yml  # backend (internal) + frontend (published)
└── .env.example
```

## Running

```bash
# host development (needs a reachable Postgres)
uv sync --project backend
uv run --project backend myhealth job
uv run --project backend myhealth anthem auth status

# containers (targets the shared ../database PostgreSQL, network `proxy`)
docker compose --env-file ../.env up -d --build
# dashboard: http://<host>:8517
```

## Key concepts

- Three PostgreSQL databases: `myhealth_auth` (tokens, patients, PKCE
  verifiers, job runs), `myhealth_anthem`, `myhealth_ucla`.
- `myhealth job` runs one data pull; `--daemon` loops on
  `JOB_INTERVAL_MINUTES` (default 360). The FastAPI app starts the daemon
  thread in its lifespan.
- OAuth2 with PKCE. The `code_verifier` is persisted to the
  `pkce_verifiers` table when an authorize URL is built and consumed on
  exchange — survives restarts and CLI/API boundary.
- Manual-paste auth: no localhost listener. Authorize → provider redirects to
  the configured `REDIRECT_URI` carrying `code` → paste the URL into the
  dashboard (or CLI) to exchange.

## Configuration

Copy `.env.example` → `.env`, or export. For containers, the compose file
maps `MYHEALTH_DB_*` from the shared Docker `.env` (`ANTHEM_DB_PASS`, etc.)
and provider credentials (`CLIENT_ID`/`CLIENT_SECRET` for Anthem,
`UCLA_CLIENT_ID`/`UCLA_SECRET` for UCLA).
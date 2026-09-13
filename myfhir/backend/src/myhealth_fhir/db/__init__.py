"""Database engine, session, and initialization for three PostgreSQL databases.

Databases:
  - myhealth_anthem  : Insurance data (EOB, claims, members, entities)
  - myhealth_ucla    : Clinical data (labs, imaging, encounters, notes)
  - myhealth_auth    : OAuth tokens (primary source)

Usage:
  from myhealth_fhir.db import get_anthem_session, get_ucla_session, get_auth_session

  with get_anthem_session() as session:
      eobs = session.query(EOB).all()

  with get_ucla_session() as session:
      labs = session.query(LabResult).all()

  with get_auth_session() as session:
      tokens = session.query(OAuthTokenRecord).all()

Backward compat:
  from myhealth_fhir.db import get_session  # defaults to anthem (most callers)
"""

import os
import json
import base64
import threading
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from myhealth_fhir.db.models_auth import (
    AuthBase,
    EntityName as AuthEntityName,
    OAuthTokenRecord as AuthOAuthTokenRecord,
    PatientRecord as AuthPatientRecord,
)
from myhealth_fhir.db.models_anthem import (
    AnthemBase,
    ClaimCareTeam,
    ClaimDiagnosis,
    ClaimItem,
    ClaimSubmission,
    EOB,
    EOBCareTeam,
    EOBDiagnosis,
    EOBItem,
    EOBItemAdjudication,
    EOBTotal,
    EntityName,
    OAuthTokenRecord as AnthemOAuthTokenRecord,
    PatientRecord as AnthemPatientRecord,
)
from myhealth_fhir.db.models_ucla import (
    CareTeam,
    ClinicalNote,
    ClinicalObservation,
    Communication,
    DiagnosticReport,
    Encounter,
    EncounterParticipant,
    EntityName as UclaEntityName,
    ImagingObservation,
    LabResult,
    MedicationAdministration,
    OAuthTokenRecord as UclaOAuthTokenRecord,
    PatientRecord as UclaPatientRecord,
    ServiceRequest,
    Specimen,
    UclaBase,
)

# ── Thread-local context for backward-compatible get_session() ────

_provider_context = threading.local()


class provider_context:
    """Context manager that sets the thread-local provider for get_session().

    Usage:
        with provider_context("ucla"):
            with get_session() as s:
                s.query(LabResult)...
    """

    def __init__(self, provider: str):
        self.provider = provider
        self._prev = None

    def __enter__(self):
        self._prev = getattr(_provider_context, "provider", None)
        _provider_context.provider = self.provider

    def __exit__(self, *args):
        _provider_context.provider = self._prev


def _resolve_provider() -> str:
    """Resolve the provider for backward-compatible get_session()."""
    return getattr(_provider_context, "provider", "anthem")


# ── URL Resolution ────────────────────────────────────────────────


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent.parent


def _resolve_db_url(database_name: str) -> str:
    """Resolve the configured PostgreSQL URI or fall back to SQLite."""
    _load_project_env()
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        # Replace the default database name with the target one
        if env_url.startswith("postgres://"):
            env_url = env_url.replace("postgres://", "postgresql+psycopg://", 1)
        if env_url.startswith("postgresql://") and "+" not in env_url:
            env_url = env_url.replace("postgresql://", "postgresql+psycopg://", 1)
        # Replace database name in URL
        # e.g. postgresql+psycopg://user:pass@host/myhealth_auth -> .../myhealth_anthem
        parts = env_url.split("/")
        if len(parts) >= 3:
            parts[-1] = database_name
            return "/".join(parts)
        return env_url

    db_user = os.environ.get("MYHEALTH_DB_USER")
    db_pass = os.environ.get("MYHEALTH_DB_PASS")
    db_host = os.environ.get("MYHEALTH_DB_HOST")
    db_port = os.environ.get("MYHEALTH_DB_PORT", "5432")
    if db_user and db_pass and db_host:
        if db_host == "db" and not Path("/.dockerenv").exists():
            db_host = "127.0.0.1"
        return (
            f"postgresql+psycopg://{quote(db_user, safe='')}:{quote(db_pass, safe='')}"
            f"@{db_host}:{db_port}/{database_name}"
        )

    # Dev fallback: SQLite files
    db_path = _project_root() / "data" / f"{database_name}.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path}"


def _load_project_env() -> None:
    """Load the repository root environment without overwriting exports."""
    load_dotenv(_project_root().parent / ".env", override=False)


# ── Engines (cached) ──────────────────────────────────────────────

_engines: dict[str, any] = {}
_init_lock = threading.RLock()
_initialized = False


def _create_engine(name: str):
    """Create and cache an engine for a database."""
    url = _resolve_db_url(name)
    is_pg = url.startswith("postgresql")
    kwargs: dict = {"echo": False}
    if is_pg:
        kwargs["pool_pre_ping"] = True
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 20
        kwargs["pool_recycle"] = 1800
    return create_engine(url, **kwargs)


def get_anthem_engine():
    if "anthem" not in _engines:
        _engines["anthem"] = _create_engine("myhealth_anthem")
    return _engines["anthem"]


def get_ucla_engine():
    if "ucla" not in _engines:
        _engines["ucla"] = _create_engine("myhealth_ucla")
    return _engines["ucla"]


def get_auth_engine():
    if "auth" not in _engines:
        _engines["auth"] = _create_engine("myhealth_auth")
    return _engines["auth"]


def get_engine():
    """Backward compat — returns anthem engine by default."""
    return get_anthem_engine()


# ── Session resolution ─────────────────────────────────────────────

# Maps a provider name to its downstream database. Adding a new Epic
# provider = one entry here (plus a profile in config/settings.py).
PROVIDER_DB: dict[str, str] = {
    "anthem": "myhealth_anthem",
    "ucla": "myhealth_ucla",
}


def get_session_for(provider: str) -> Session:
    """Return the session for a provider's downstream database.

    ``anthem`` → myhealth_anthem, ``ucla`` → myhealth_ucla, etc.
    Falls back to the anthem session for unknown/legacy names.
    """
    database = PROVIDER_DB.get(provider)
    if database == "myhealth_ucla":
        return get_ucla_session()
    if database == "myhealth_auth":
        return get_auth_session()
    return get_anthem_session()


def get_clinical_session(provider: str) -> Session:
    """Return the session for an Epic (clinical) provider's database.

    ``ucla`` → myhealth_ucla. Unknown clinical providers fall back to ucla.
    """
    database = PROVIDER_DB.get(provider)
    if database == "myhealth_anthem":
        return get_anthem_session()
    return get_ucla_session()


# ── Sessions ──────────────────────────────────────────────────────


def get_anthem_session() -> Session:
    """Session for the anthem (insurance) database."""
    return Session(get_anthem_engine())


def get_ucla_session() -> Session:
    """Session for the ucla (clinical) database."""
    return Session(get_ucla_engine())


def get_auth_session() -> Session:
    """Session for the auth (OAuth tokens) database."""
    return Session(get_auth_engine())


def get_session() -> Session:
    """Backward compat — resolves to the appropriate session based on context.

    Defaults to anthem. Use provider_context("ucla") to override.
    Prefer explicit get_anthem_session() / get_ucla_session() / get_auth_session().
    """
    provider = _resolve_provider()
    if provider == "ucla":
        return get_ucla_session()
    if provider == "auth":
        return get_auth_session()
    return get_anthem_session()


def is_postgres() -> bool:
    url = _resolve_db_url("myhealth_auth")
    return url.startswith("postgresql")


# ── Initialization ────────────────────────────────────────────────


def init_db():
    """Create all tables and views in all three databases."""
    global _initialized
    with _init_lock:
        if _initialized:
            return
        _init_db_once()
        _initialized = True


def _init_db_once():
    """Perform one process-wide schema initialization pass."""
    # Auth DB
    auth_engine = get_auth_engine()
    AuthBase.metadata.create_all(auth_engine)

    # Data databases
    anthem_engine = get_anthem_engine()
    AnthemBase.metadata.create_all(anthem_engine)
    ucla_engine = get_ucla_engine()
    UclaBase.metadata.create_all(ucla_engine)

    if is_postgres():
        with auth_engine.begin() as conn:
            conn.execute(text("DROP VIEW IF EXISTS member_claims_summary, member_claims_recon, claim_items, claim_submissions, eob_items, eob_claims CASCADE"))
        with anthem_engine.begin() as conn:
            conn.execute(text("DROP VIEW IF EXISTS member_claims, member_claims_summary, member_claims_recon, claim_items, claim_submissions, eob_items, eob_claims, vw_eob_all, vw_claims_all CASCADE"))
        with ucla_engine.begin() as conn:
            conn.execute(text("DROP VIEW IF EXISTS clinical_overview, lab_results CASCADE"))
        _migrate_auth_schema(auth_engine)
        _migrate_anthem_schema(anthem_engine)
        _migrate_ucla_schema(ucla_engine)
        _migrate_raw_json_unpack_anthem(anthem_engine)
        _migrate_raw_json_unpack_ucla(ucla_engine)
        _backfill_auth_patient_registry(auth_engine)
        _backfill_fhir_identity_registry(anthem_engine, "anthem")
        _backfill_fhir_identity_registry(ucla_engine, "ucla")

    _create_anthem_views(anthem_engine)
    _create_ucla_views(ucla_engine)

    # Sync oauth_tokens replicas
    _sync_oauth_tokens_replica()


# ── Schema Migration ──────────────────────────────────────────────


def _migrate_auth_schema(engine):
    """Add normalized patient references and migrate legacy patient names."""
    _migrate_identity_schema(engine, "auth")
    with engine.begin() as conn:
        columns = {row[0] for row in conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'oauth_tokens'"
        ))}
        if "patient_name" not in columns:
            return
        conn.execute(text("""
            INSERT INTO entity_names(entity_ref, provider, entity_type, entity_id, name, display)
            SELECT provider || chr(58) || 'Patient' || chr(58) || patient_id,
                   provider, 'Patient', patient_id, NULLIF(BTRIM(patient_name), ''),
                   NULLIF(BTRIM(patient_name), '')
            FROM oauth_tokens
            WHERE NULLIF(BTRIM(patient_name), '') IS NOT NULL
            ON CONFLICT (entity_ref) DO UPDATE SET
              name = COALESCE(entity_names.name, EXCLUDED.name),
              display = COALESCE(entity_names.display, EXCLUDED.display)
        """))
        conn.execute(text("""
            INSERT INTO patients(patient_id, provider, entity_ref)
            SELECT patient_id, provider, provider || chr(58) || 'Patient' || chr(58) || patient_id
            FROM oauth_tokens
            ON CONFLICT (patient_id, provider) DO UPDATE SET entity_ref = EXCLUDED.entity_ref
        """))
        conn.execute(text("ALTER TABLE oauth_tokens DROP COLUMN patient_name"))


def _migrate_ucla_schema(engine):
    """Add normalized UCLA identity references and backfill legacy names.

    Also applies additive column migrations for the extended clinical schema:
    ``raw_json``/``component_value`` on lab/imaging observations, ``source``/``source_id``
    provenance tagging on the FHIR/EHI-backed tables, and ensures the newer
    encounter/note/immunization/medication tables exist. ``create_all()`` won't add
    columns to existing tables, so ALTER TABLE is required.
    """
    _migrate_identity_schema(engine, "ucla")
    UclaBase.metadata.create_all(engine)  # ensure new tables (medication_administration, etc.)
    _migrate_ucla_extended_schema(engine)


def _migrate_ucla_extended_schema(engine):
    """Additive ALTERs for the extended UCLA clinical schema (PostgreSQL-only)."""
    with engine.begin() as conn:
        # Provenance tagging (source / source_id) — existing data defaults to 'fhir'
        for table in (
            "encounter", "clinical_observation", "clinical_note", "condition",
            "medication_request", "immunization",
        ):
            conn.execute(text(f"""
                ALTER TABLE {table}
                  ADD COLUMN IF NOT EXISTS source VARCHAR(10) DEFAULT 'fhir',
                  ADD COLUMN IF NOT EXISTS source_id TEXT
            """))
        # Lab / imaging fidelity columns
        conn.execute(text("""
            ALTER TABLE lab_result
              ADD COLUMN IF NOT EXISTS component_value TEXT,
              ADD COLUMN IF NOT EXISTS raw_json TEXT
        """))
        conn.execute(text("""
            ALTER TABLE imaging_observation
              ADD COLUMN IF NOT EXISTS component_value TEXT,
              ADD COLUMN IF NOT EXISTS raw_json TEXT
        """))
        conn.execute(text("""
            ALTER TABLE clinical_observation
              ADD COLUMN IF NOT EXISTS component_value TEXT,
              ADD COLUMN IF NOT EXISTS raw_json TEXT
        """))
        # Better FK column widths for newer content
        conn.execute(text("ALTER TABLE medication_request ALTER COLUMN medication_code TYPE VARCHAR(100)"))


def _validate_identity_backfill(engine, table: str, legacy_column: str, ref_column: str) -> None:
    """Fail closed if a non-empty legacy value was not preserved in the registry."""
    with engine.connect() as conn:
        missing = conn.execute(text(f"""
            SELECT COUNT(*) FROM {table} t
            WHERE NULLIF(BTRIM(t.{legacy_column}), '') IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM entity_names e WHERE e.entity_ref = t.{ref_column})
        """)).scalar_one()
        if missing:
            raise RuntimeError(f"identity backfill incomplete for {table}.{legacy_column}")


def _migrate_identity_schema(engine, provider: str) -> None:
    """Create the registry, backfill legacy identity columns, then remove them."""
    replacements = {
        "anthem": {
            "patients": {"patient_name": "entity_ref"},
            "member_claim_submission": {"provider_name": "provider_ref"},
        },
        "ucla": {
            "patients": {"patient_name": "entity_ref"},
            "condition": {"asserter_name": "asserter_ref"},
            "procedure_record": {"performer_name": "performer_ref"},
            "medication_request": {"requester_name": "requester_ref"},
            "allergy_intolerance": {"recorder_name": "recorder_ref"},
            "immunization": {"performer_name": "performer_ref"},
            "care_plan": {"author_name": "author_ref"},
            "document_reference": {"author_name": "author_ref"},
            "diagnostic_report": {"performer": "performer_ref"},
            "clinical_note": {"author": "author_ref"},
            "family_member_history": {"name": "family_member_ref"},
            "encounter": {"patient_name": "patient_ref"},
        },
        "auth": {"patients": {"patient_name": "entity_ref"}},
    }[provider]
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS entity_names (
              entity_ref VARCHAR(255) PRIMARY KEY,
              provider VARCHAR(20) NOT NULL,
              entity_type VARCHAR(50) NOT NULL,
              entity_id VARCHAR(200) NOT NULL,
              name TEXT,
              npi VARCHAR(20),
              display TEXT,
              loaded_at TIMESTAMP DEFAULT now(),
              CONSTRAINT entity_names_provider_type_id UNIQUE(provider, entity_type, entity_id)
            )
        """))
        registry_columns = {row[0] for row in conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'entity_names'"
        ))}
        if "entity_ref" not in registry_columns:
            conn.execute(text("ALTER TABLE entity_names ADD COLUMN entity_ref VARCHAR(255)"))
        if "provider" not in registry_columns:
            conn.execute(text("ALTER TABLE entity_names ADD COLUMN provider VARCHAR(20)"))
        conn.execute(text("""
            UPDATE entity_names
            SET provider = COALESCE(provider, :provider),
                entity_ref = COALESCE(
                    entity_ref,
                    COALESCE(provider, :provider) || chr(58) ||
                    COALESCE(entity_type, 'Unknown') || chr(58) ||
                    COALESCE(entity_id, ctid::text)
                )
            WHERE entity_ref IS NULL OR provider IS NULL
        """), {"provider": provider})
        conn.execute(text("""
            UPDATE entity_names
            SET entity_type = INITCAP(entity_type),
                entity_ref = provider || chr(58) || INITCAP(entity_type) || chr(58) || entity_id
            WHERE entity_type IS NOT NULL AND entity_id IS NOT NULL
        """))
        conn.execute(text("""
            UPDATE entity_names
            SET name = NULLIF(BTRIM(name), ''),
                display = NULLIF(BTRIM(display), '')
        """))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS entity_names_entity_ref_key ON entity_names(entity_ref)"))
        conn.execute(text("""
            INSERT INTO entity_names(entity_ref, provider, entity_type, entity_id, name, display)
            SELECT provider || chr(58) || 'Patient' || chr(58) || regexp_replace(entity_id, chr(58) || 'patient_name$', ''),
                   provider, 'Patient', regexp_replace(entity_id, chr(58) || 'patient_name$', ''), name, display
            FROM entity_names
            WHERE entity_type = 'Patient' AND entity_id LIKE '%' || chr(58) || 'patient_name'
            ON CONFLICT (entity_ref) DO UPDATE SET name = COALESCE(entity_names.name, EXCLUDED.name)
        """))
        conn.execute(text("DELETE FROM entity_names WHERE entity_type = 'Patient' AND entity_id LIKE '%' || chr(58) || 'patient_name'"))
        conn.execute(text("ALTER TABLE entity_names ALTER COLUMN entity_ref SET NOT NULL"))
        conn.execute(text("ALTER TABLE patients ADD COLUMN IF NOT EXISTS entity_ref VARCHAR(255)"))
        for table, columns in replacements.items():
            existing = {row[0] for row in conn.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_name = :table"
            ), {"table": table})}
            id_col = "fhir_id" if "fhir_id" in existing else (
                "id" if "id" in existing else "patient_id"
            )
            for legacy, replacement in columns.items():
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {replacement} VARCHAR(255)"))
                if legacy not in existing:
                    continue
                entity_type = "Patient" if legacy == "patient_name" else legacy.removesuffix("_name").title()
                if table == "member_claim_submission":
                    key = f"member_submission:' || {id_col} || ':provider"
                    ref_expr = f"'anthem:MemberSubmission:' || {id_col} || chr(58) || 'provider'"
                    id_expr = f"{id_col}::text || chr(58) || 'provider'"
                else:
                    key = f"'{provider}:{entity_type}:' || {id_col} || chr(58) || :legacy"
                    ref_expr = key.format(provider=provider)
                    id_expr = f"{id_col}::text || chr(58) || :legacy"
                conn.execute(text(f"""
                    INSERT INTO entity_names(entity_ref, provider, entity_type, entity_id, name)
                    SELECT {ref_expr}, :provider, :entity_type, {id_expr}, NULLIF(BTRIM({legacy}), '')
                    FROM {table} WHERE NULLIF(BTRIM({legacy}), '') IS NOT NULL
                    ON CONFLICT (entity_ref) DO UPDATE SET name = EXCLUDED.name
                """), {"provider": provider, "entity_type": entity_type, "legacy": legacy})
                conn.execute(text(f"""
                    UPDATE {table} SET {replacement} = {ref_expr}
                    WHERE {replacement} IS NULL AND NULLIF(BTRIM({legacy}), '') IS NOT NULL
                """), {"legacy": legacy})
                missing = conn.execute(text(f"""
                    SELECT COUNT(*) FROM {table} t
                    WHERE NULLIF(BTRIM(t.{legacy}), '') IS NOT NULL
                      AND NOT EXISTS (SELECT 1 FROM entity_names e WHERE e.entity_ref = t.{replacement})
                """)).scalar_one()
                if missing:
                    raise RuntimeError(f"identity backfill incomplete for {table}.{legacy}")
                conn.execute(text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {legacy}"))


def _backfill_auth_patient_registry(engine) -> None:
    """Create patient mappings from existing OAuth JWT claims when needed."""
    from myhealth_fhir.db.identity import entity_ref

    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT patient_id, provider, id_token FROM oauth_tokens WHERE id_token IS NOT NULL"
        ))
        for patient_id, provider, id_token in rows:
            try:
                payload = id_token.split(".")[1]
                payload += "=" * (-len(payload) % 4)
                claims = json.loads(base64.urlsafe_b64decode(payload))
            except (IndexError, ValueError, TypeError, json.JSONDecodeError):
                continue
            name = claims.get("name")
            if not name:
                name = " ".join(str(value) for value in (
                    claims.get("given_name"), claims.get("family_name")
                ) if value).strip() or None
            ref = entity_ref(provider, "Patient", patient_id)
            conn.execute(text("""
                INSERT INTO entity_names(entity_ref, provider, entity_type, entity_id, name, display)
                VALUES (:ref, :provider, 'Patient', :patient_id, :name, :name)
                ON CONFLICT (entity_ref) DO UPDATE SET name = COALESCE(entity_names.name, EXCLUDED.name)
            """), {"ref": ref, "provider": provider, "patient_id": patient_id, "name": name})
            conn.execute(text("""
                INSERT INTO patients(patient_id, provider, entity_ref)
                VALUES (:patient_id, :provider, :ref)
                ON CONFLICT (patient_id, provider) DO UPDATE SET entity_ref = EXCLUDED.entity_ref
            """), {"patient_id": patient_id, "provider": provider, "ref": ref})


def _migrate_anthem_schema(engine):
    """Add member-submitted classification columns, backfill from raw_json, and repair defaults.

    create_all() won't add columns to existing tables, so ALTER TABLE here.
    Adds ``submission_origin`` / ``is_out_of_network`` to ``eob`` and ``claim_submission``,
    backfills them from payee/claim-number/header adjudication, and repairs the
    ``updated_at`` default on ``member_claim_submission`` (sets it to ``now()``).
    Runs before views are (re)created so the member_claims view can reference them.
    PostgreSQL-only: uses ``ADD COLUMN IF NOT EXISTS``, ``jsonb_array_elements``, ``::jsonb``, ``~`` regex.
    """
    _migrate_identity_schema(engine, "anthem")
    with engine.connect() as conn:
        for table, column, kind in (
            ("eob", "patient_ref", "Patient"),
            ("eob", "provider_ref", "Organization"),
            ("eob", "payee_ref", "Organization"),
            ("claim_submission", "patient_ref", "Patient"),
            ("claim_submission", "provider_ref", "Organization"),
            ("claim_submission", "insurer_ref", "Organization"),
            ("eob_care_team", "provider_ref", "Practitioner"),
            ("claim_care_team", "provider_ref", "Practitioner"),
        ):
            conn.execute(text(f"""
                UPDATE {table}
                SET {column} = 'anthem:' || :kind || ':' || {column}
                WHERE {column} IS NOT NULL AND {column} NOT LIKE 'anthem:%'
            """), {"kind": kind})
        conn.execute(text("""
            ALTER TABLE eob
               ADD COLUMN IF NOT EXISTS submission_origin VARCHAR(20) NOT NULL DEFAULT 'provider',
               ADD COLUMN IF NOT EXISTS is_out_of_network BOOLEAN NOT NULL DEFAULT false
        """))
        conn.execute(text("""
            ALTER TABLE claim_submission
               ADD COLUMN IF NOT EXISTS submission_origin VARCHAR(20) NOT NULL DEFAULT 'provider',
               ADD COLUMN IF NOT EXISTS is_out_of_network BOOLEAN NOT NULL DEFAULT false
        """))
        # Add claim_received_date from CARIN BB supportingInfo.clmrecvddate
        conn.execute(text("ALTER TABLE eob ADD COLUMN IF NOT EXISTS claim_received_date DATE"))
        conn.execute(text("""
            UPDATE eob e SET claim_received_date = (
                SELECT (si->>'timingDate')::date
                FROM jsonb_array_elements(e.raw_json::jsonb -> 'supportingInfo') si
                WHERE si->'category'->'coding'->0->>'code' = 'clmrecvddate'
                LIMIT 1
            )
            WHERE e.raw_json IS NOT NULL
              AND e.claim_received_date IS NULL
              AND (e.raw_json::jsonb -> 'supportingInfo') IS NOT NULL
        """))
        conn.execute(text("""
            ALTER TABLE member_claim_submission
              ALTER COLUMN updated_at SET DEFAULT now()
        """))
        # Backfill eob.submission_origin from existing payee / claim number
        # (first repair payee refs that were mis-typed as Organization when the
        # payee party was actually the member — Anthem pays the subscriber via
        # payee.party = Patient/<id>)
        conn.execute(text("""
            UPDATE eob
            SET payee_ref = 'anthem:Patient:' || split_part(
                    raw_json::jsonb -> 'payee' -> 'party' ->> 'reference', '/', 2)
            WHERE raw_json::jsonb -> 'payee' -> 'party' ->> 'reference' LIKE 'Patient/%'
              AND payee_ref LIKE 'anthem:Organization:%'
        """))
        conn.execute(text("""
            UPDATE claim_submission
            SET payee_ref = 'anthem:Patient:' || split_part(
                    raw_json::jsonb -> 'payee' -> 'party' ->> 'reference', '/', 2)
            WHERE raw_json::jsonb -> 'payee' -> 'party' ->> 'reference' LIKE 'Patient/%'
              AND (payee_ref LIKE 'anthem:Organization:%' OR payee_ref NOT LIKE 'anthem:%')
        """))
        conn.execute(text("""
            UPDATE eob SET submission_origin = 'member'
            WHERE (payee_type IN ('beneficiary', 'subscriber') AND payee_ref LIKE 'anthem:Patient:%')
               OR claim_number ~ '^(DELTADENTAL|VSP|MEDCO)'
        """))
        # Repair care-team refs mis-typed as Practitioner when the FHIR
        # careTeam[].provider reference was actually Organization/other.
        conn.execute(text("""
            UPDATE eob_care_team ct
            SET provider_ref = 'anthem:' ||
                  split_part(raw_ref, '/', 1) || ':' || split_part(raw_ref, '/', 2)
            FROM eob e, jsonb_array_elements(e.raw_json::jsonb -> 'careTeam') c(raw)
            CROSS JOIN LATERAL (SELECT c.raw -> 'provider' ->> 'reference' AS raw_ref) x
            WHERE e.id = ct.eob_id
              AND x.raw_ref LIKE '%/%'
              AND ct.sequence::text = c.raw ->> 'sequence'
              AND ct.provider_ref = 'anthem:Practitioner:' || split_part(x.raw_ref, '/', 2)
              AND split_part(x.raw_ref, '/', 1) <> 'Practitioner'
        """))
        conn.execute(text("""
            UPDATE claim_care_team ct
            SET provider_ref = 'anthem:' ||
                  split_part(raw_ref, '/', 1) || ':' || split_part(raw_ref, '/', 2)
            FROM claim_submission cs, jsonb_array_elements(cs.raw_json::jsonb -> 'careTeam') c(raw)
            CROSS JOIN LATERAL (SELECT c.raw -> 'provider' ->> 'reference' AS raw_ref) x
            WHERE cs.id = ct.claim_id
              AND x.raw_ref LIKE '%/%'
              AND ct.sequence::text = c.raw ->> 'sequence'
              AND ct.provider_ref = 'anthem:Practitioner:' || split_part(x.raw_ref, '/', 2)
              AND split_part(x.raw_ref, '/', 1) <> 'Practitioner'
        """))
        # Backfill claim_number / claim_adjustment_key for rows written before
        # the parser gained the system-URL fallback (some Anthem Claim
        # resources carry identifiers with no type coding, only
        # .../clm_nbr and .../claimAdjustmentKey system URLs).
        conn.execute(text("""
            UPDATE claim_submission c
            SET claim_number = ident.value
            FROM (
                SELECT cs.id,
                       (SELECT i->>'value' FROM jsonb_array_elements(cs.raw_json::jsonb -> 'identifier') i
                        WHERE (i->>'system') LIKE '%clm_nbr' LIMIT 1) AS value
                FROM claim_submission cs
                WHERE cs.claim_number IS NULL
            ) ident
            WHERE c.id = ident.id AND ident.value IS NOT NULL
        """))
        conn.execute(text("""
            UPDATE claim_submission c
            SET claim_adjustment_key = ident.value
            FROM (
                SELECT cs.id,
                       (SELECT i->>'value' FROM jsonb_array_elements(cs.raw_json::jsonb -> 'identifier') i
                        WHERE (i->>'system') LIKE '%claimAdjustmentKey' LIMIT 1) AS value
                FROM claim_submission cs
                WHERE cs.claim_adjustment_key IS NULL
            ) ident
            WHERE c.id = ident.id AND ident.value IS NOT NULL
        """))
        # Backfill eob.is_out_of_network from header adjudication
        conn.execute(text("""
            UPDATE eob SET is_out_of_network = true
            WHERE EXISTS (
                SELECT 1 FROM jsonb_array_elements(raw_json::jsonb -> 'adjudication') a
                WHERE a -> 'category' -> 'coding' -> 0 ->> 'code' = 'billingnetworkstatus'
                  AND a -> 'reason' -> 'coding' -> 0 ->> 'code' = 'outofnetwork'
            )
        """))
        # Backfill claim_submission.submission_origin from payee / clm_nbr
        conn.execute(text("""
            UPDATE claim_submission SET submission_origin = 'member'
            WHERE (raw_json::jsonb -> 'payee' -> 'type' -> 'coding' -> 0 ->> 'code' IN ('beneficiary', 'subscriber')
                   AND raw_json::jsonb -> 'payee' -> 'party' ->> 'reference' LIKE 'Patient/%')
                OR claim_number ~ '^(DELTADENTAL|VSP|MEDCO)'
        """))

        # ── vw_member_submitted — member-submitted EOB claims (item-level) ──
        conn.execute(text("DROP VIEW IF EXISTS vw_member_submitted"))
        conn.execute(text("""
            CREATE VIEW vw_member_submitted AS
            SELECT
              -- Claim identifiers & status
              e.id AS eob_id,
              e.claim_number,
              e.status,
              e.submission_origin,
              e.is_out_of_network,
              -- Money: billed vs allowed
              i.submitted_amount,
              i.allowed_amount,
              -- Money: where it went
              i.paid_provider,
              i.paid_patient,
              i.member_liability,
              i.noncovered AS item_noncovered,
              -- Money: breakdown
              i.deductible AS item_deductible,
              i.coinsurance AS item_coinsurance,
              i.copay AS item_copay,
              i.discount AS item_discount,
              -- Item procedures & service date
              i.sequence AS item_seq,
              i.hcpcs_code,
              i.hcpcs_display,
              i.modifier_codes,
              i.quantity,
              i.serviced_date,
              -- Diagnoses
              COALESCE((SELECT STRING_AGG(icd_code, ', ') FROM eob_diagnosis d WHERE d.eob_id = e.id), '') AS icd_codes,
              COALESCE((SELECT STRING_AGG(icd_display, ' | ') FROM eob_diagnosis d WHERE d.eob_id = e.id), '') AS icd_displays,
              -- Payment details
              i.payment_status,
              i.adjustment_reason,
              -- Provider context
              en_prov.name AS provider_name,
              en_payee.name AS payee_name,
              COALESCE((SELECT STRING_AGG(en_ct.name, ', ')
                        FROM eob_care_team ct
                        LEFT JOIN entity_names en_ct ON en_ct.entity_ref = ct.provider_ref
                        WHERE ct.eob_id = e.id), '') AS care_team_providers,
              COALESCE((SELECT STRING_AGG(role_display, ', ') FROM eob_care_team ct WHERE ct.eob_id = e.id), '') AS care_team_roles,
              -- Patient
              split_part(e.patient_ref, ':', 3) AS patient_id,
              en_patient.name AS patient_name,
              -- EOB dates
              e.created_date,
              e.billable_period_start,
              e.billable_period_end,
              -- EOB-level totals (repeated per row)
              (SELECT amount FROM eob_total WHERE eob_id = e.id AND category_code = 'submitted' LIMIT 1) AS total_submitted,
              (SELECT amount FROM eob_total WHERE eob_id = e.id AND category_code = 'benefit' LIMIT 1) AS total_benefit,
              (SELECT amount FROM eob_total WHERE eob_id = e.id AND category_code = 'deductible' LIMIT 1) AS total_deductible,
              (SELECT amount FROM eob_total WHERE eob_id = e.id AND category_code = 'coinsurance' LIMIT 1) AS total_coinsurance,
              (SELECT SUM(member_liability) FROM eob_item WHERE eob_id = e.id) AS total_member_liability,
              (SELECT SUM(noncovered) FROM eob_item WHERE eob_id = e.id) AS total_noncovered,
              -- Signals (member-submission detection audit trail)
              ARRAY_TO_STRING(ARRAY_REMOVE(ARRAY[
                CASE WHEN e.submission_origin = 'member' THEN 'origin' END,
                CASE WHEN lower(coalesce(en_prov.name,'')) LIKE '%member%' THEN 'provider_name' END,
                CASE WHEN lower(coalesce(en_payee.name,'')) LIKE '%member%' THEN 'payee_name' END,
                CASE WHEN EXISTS (
                         SELECT 1 FROM eob_care_team ct
                         LEFT JOIN entity_names en_ct ON en_ct.entity_ref = ct.provider_ref
                         WHERE ct.eob_id = e.id
                           AND (lower(coalesce(en_ct.name,'')) LIKE '%member%'
                                OR lower(coalesce(ct.role_code,'')) LIKE '%member%'
                                OR lower(coalesce(ct.role_display,'')) LIKE '%member%')
                       ) THEN 'care_team' END
              ], NULL), ';') AS member_signals
            FROM eob e
            JOIN eob_item i ON i.eob_id = e.id
            LEFT JOIN oauth_tokens ot ON ot.patient_id = split_part(e.patient_ref, ':', 3)
            LEFT JOIN patients p ON p.entity_ref = e.patient_ref AND p.provider = ot.provider
            LEFT JOIN entity_names en_patient ON en_patient.entity_ref = p.entity_ref
            LEFT JOIN entity_names en_prov ON en_prov.entity_ref = e.provider_ref
            LEFT JOIN entity_names en_payee ON en_payee.entity_ref = e.payee_ref
            WHERE e.submission_origin = 'member'
               OR lower(coalesce(en_prov.name,'')) LIKE '%member%'
               OR lower(coalesce(en_payee.name,'')) LIKE '%member%'
               OR EXISTS (
                    SELECT 1 FROM eob_care_team ct
                    LEFT JOIN entity_names en_ct ON en_ct.entity_ref = ct.provider_ref
                    WHERE ct.eob_id = e.id
                      AND (lower(coalesce(en_ct.name,'')) LIKE '%member%'
                           OR lower(coalesce(ct.role_code,'')) LIKE '%member%'
                           OR lower(coalesce(ct.role_display,'')) LIKE '%member%')
                  )
        """))

        conn.commit()


def _migrate_raw_json_unpack_anthem(engine):
    """Additively unpack claim_submission/eob raw_json into named columns + child tables."""
    with engine.begin() as conn:
        conn.execute(text("""
            ALTER TABLE eob
              ADD COLUMN IF NOT EXISTS insurer_ref TEXT,
              ADD COLUMN IF NOT EXISTS payment_currency VARCHAR(3),
              ADD COLUMN IF NOT EXISTS payment_adjustment_code VARCHAR(100),
              ADD COLUMN IF NOT EXISTS payment_adjustment_display TEXT,
              ADD COLUMN IF NOT EXISTS preauth_refs TEXT,
              ADD COLUMN IF NOT EXISTS payer_display TEXT
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS eob_identifier (
              id SERIAL PRIMARY KEY,
              eob_id TEXT NOT NULL REFERENCES eob(id) ON DELETE CASCADE,
              seq INTEGER,
              system TEXT,
              value TEXT,
              use VARCHAR(20),
              type_code VARCHAR(100),
              type_display TEXT
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS eob_identifier_eob_id_idx ON eob_identifier(eob_id)"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS eob_adjudication (
              id SERIAL PRIMARY KEY,
              eob_id TEXT NOT NULL REFERENCES eob(id) ON DELETE CASCADE,
              seq INTEGER,
              category_code VARCHAR(100),
              category_display TEXT,
              amount FLOAT,
              currency VARCHAR(3),
              value_units INTEGER,
              reason_code VARCHAR(100),
              reason_display TEXT
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS eob_adjudication_eob_id_idx ON eob_adjudication(eob_id)"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS eob_supporting_info (
              id SERIAL PRIMARY KEY,
              eob_id TEXT NOT NULL REFERENCES eob(id) ON DELETE CASCADE,
              seq INTEGER,
              category_code VARCHAR(100),
              category_display TEXT,
              code_code VARCHAR(100),
              code_display TEXT,
              timing_date DATE,
              timing_period_start DATE,
              timing_period_end DATE,
              value_string TEXT
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS eob_supporting_info_eob_id_idx ON eob_supporting_info(eob_id)"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS eob_procedure (
              id SERIAL PRIMARY KEY,
              eob_id TEXT NOT NULL REFERENCES eob(id) ON DELETE CASCADE,
              seq INTEGER,
              service_sequence INTEGER,
              service_date DATE,
              code_code VARCHAR(100),
              code_display TEXT,
              type_code VARCHAR(100),
              type_display TEXT
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS eob_procedure_eob_id_idx ON eob_procedure(eob_id)"))
        conn.execute(text("""
            ALTER TABLE claim_submission
              ADD COLUMN IF NOT EXISTS claim_number TEXT,
              ADD COLUMN IF NOT EXISTS claim_adjustment_key TEXT,
              ADD COLUMN IF NOT EXISTS sub_type_code VARCHAR(100),
              ADD COLUMN IF NOT EXISTS sub_type_display TEXT,
              ADD COLUMN IF NOT EXISTS payee_type VARCHAR(50),
              ADD COLUMN IF NOT EXISTS payee_ref TEXT,
              ADD COLUMN IF NOT EXISTS coverage_ref TEXT,
              ADD COLUMN IF NOT EXISTS preauth_refs TEXT,
              ADD COLUMN IF NOT EXISTS prescription_ref TEXT,
              ADD COLUMN IF NOT EXISTS priority_display TEXT,
              ADD COLUMN IF NOT EXISTS adjudication_date DATE,
              ADD COLUMN IF NOT EXISTS adjudication_status_code VARCHAR(50),
              ADD COLUMN IF NOT EXISTS action_date DATE,
              ADD COLUMN IF NOT EXISTS action_type_code VARCHAR(50),
              ADD COLUMN IF NOT EXISTS adjustment_number TEXT,
              ADD COLUMN IF NOT EXISTS claim_class_code VARCHAR(50),
              ADD COLUMN IF NOT EXISTS denial_reason_code VARCHAR(50),
              ADD COLUMN IF NOT EXISTS line_status_code VARCHAR(50),
              ADD COLUMN IF NOT EXISTS line_status_display TEXT,
              ADD COLUMN IF NOT EXISTS paid_date DATE,
              ADD COLUMN IF NOT EXISTS system_of_record_code VARCHAR(50),
              ADD COLUMN IF NOT EXISTS discharge_status_code VARCHAR(50),
              ADD COLUMN IF NOT EXISTS document_control_number TEXT,
              ADD COLUMN IF NOT EXISTS external_load_code VARCHAR(50),
              ADD COLUMN IF NOT EXISTS in_patient VARCHAR(10),
              ADD COLUMN IF NOT EXISTS length_of_stay INTEGER,
              ADD COLUMN IF NOT EXISTS network_identifier_code VARCHAR(50),
              ADD COLUMN IF NOT EXISTS place_of_service_code VARCHAR(50),
              ADD COLUMN IF NOT EXISTS place_of_service_display TEXT,
              ADD COLUMN IF NOT EXISTS pps_code VARCHAR(50),
              ADD COLUMN IF NOT EXISTS source_billing_provider_id TEXT,
              ADD COLUMN IF NOT EXISTS source_npi VARCHAR(20),
              ADD COLUMN IF NOT EXISTS total_diag_code_count INTEGER,
              ADD COLUMN IF NOT EXISTS total_paid_amount FLOAT,
              ADD COLUMN IF NOT EXISTS master_consumer_id TEXT,
              ADD COLUMN IF NOT EXISTS mbr_key TEXT,
              ADD COLUMN IF NOT EXISTS ipt_facility_number TEXT,
              ADD COLUMN IF NOT EXISTS ipt_home_code VARCHAR(50),
              ADD COLUMN IF NOT EXISTS point_of_origin_code VARCHAR(50),
              ADD COLUMN IF NOT EXISTS priority_admit_type_code VARCHAR(50),
              ADD COLUMN IF NOT EXISTS dispensed_brand_generic_code VARCHAR(50)
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS claim_identifier (
              id SERIAL PRIMARY KEY,
              claim_id TEXT NOT NULL REFERENCES claim_submission(id) ON DELETE CASCADE,
              seq INTEGER,
              system TEXT,
              value TEXT,
              use VARCHAR(20),
              type_code VARCHAR(100),
              type_display TEXT
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS claim_identifier_claim_id_idx ON claim_identifier(claim_id)"))


def _migrate_raw_json_unpack_ucla(engine):
    """Additively unpack UCLA raw_json into named columns + child tables."""
    with engine.begin() as conn:
        conn.execute(text("""
            ALTER TABLE encounter
              ADD COLUMN IF NOT EXISTS type_code VARCHAR(100),
              ADD COLUMN IF NOT EXISTS type_display TEXT,
              ADD COLUMN IF NOT EXISTS class_display TEXT,
              ADD COLUMN IF NOT EXISTS admit_source_code VARCHAR(100),
              ADD COLUMN IF NOT EXISTS admit_source_display TEXT,
              ADD COLUMN IF NOT EXISTS discharge_disposition_code VARCHAR(100),
              ADD COLUMN IF NOT EXISTS discharge_disposition_display TEXT,
              ADD COLUMN IF NOT EXISTS service_type_display TEXT,
              ADD COLUMN IF NOT EXISTS part_of_ref TEXT,
              ADD COLUMN IF NOT EXISTS account_ids TEXT,
              ADD COLUMN IF NOT EXISTS accident_related BOOLEAN
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS encounter_identifier (
              id SERIAL PRIMARY KEY,
              encounter_id TEXT NOT NULL REFERENCES encounter(id) ON DELETE CASCADE,
              seq INTEGER,
              system TEXT,
              value TEXT,
              use VARCHAR(20)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS encounter_identifier_encounter_id_idx ON encounter_identifier(encounter_id)"))
        conn.execute(text("""
            ALTER TABLE encounter_participant
              ADD COLUMN IF NOT EXISTS type_code VARCHAR(100),
              ADD COLUMN IF NOT EXISTS period_start TIMESTAMP,
              ADD COLUMN IF NOT EXISTS period_end TIMESTAMP
        """))
        conn.execute(text("""
            ALTER TABLE diagnostic_report
              ADD COLUMN IF NOT EXISTS conclusion_code VARCHAR(100),
              ADD COLUMN IF NOT EXISTS conclusion_code_display TEXT,
              ADD COLUMN IF NOT EXISTS interpreter_ref TEXT,
              ADD COLUMN IF NOT EXISTS interpreter_display TEXT,
              ADD COLUMN IF NOT EXISTS presented_form_url TEXT,
              ADD COLUMN IF NOT EXISTS presented_form_title TEXT,
              ADD COLUMN IF NOT EXISTS presented_form_type TEXT,
              ADD COLUMN IF NOT EXISTS category_code VARCHAR(100)
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS diagnostic_report_identifier (
              id SERIAL PRIMARY KEY,
              report_id TEXT NOT NULL REFERENCES diagnostic_report(id) ON DELETE CASCADE,
              seq INTEGER,
              system TEXT,
              value TEXT,
              use VARCHAR(20)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS diagnostic_report_identifier_report_id_idx ON diagnostic_report_identifier(report_id)"))
        conn.execute(text("""
            ALTER TABLE lab_result
              ADD COLUMN IF NOT EXISTS category_code VARCHAR(100),
              ADD COLUMN IF NOT EXISTS category_display TEXT,
              ADD COLUMN IF NOT EXISTS based_on_ref TEXT,
              ADD COLUMN IF NOT EXISTS specimen_ref TEXT,
              ADD COLUMN IF NOT EXISTS encounter_ref TEXT,
              ADD COLUMN IF NOT EXISTS issued TIMESTAMP,
              ADD COLUMN IF NOT EXISTS note_text TEXT,
              ADD COLUMN IF NOT EXISTS method_display TEXT,
              ADD COLUMN IF NOT EXISTS body_site TEXT,
              ADD COLUMN IF NOT EXISTS data_absent_reason_code VARCHAR(100),
              ADD COLUMN IF NOT EXISTS data_absent_reason_display TEXT,
              ADD COLUMN IF NOT EXISTS value_code VARCHAR(100),
              ADD COLUMN IF NOT EXISTS value_display TEXT,
              ADD COLUMN IF NOT EXISTS value_comparator VARCHAR(10)
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS lab_result_component (
              id SERIAL PRIMARY KEY,
              lab_id INTEGER NOT NULL REFERENCES lab_result(id) ON DELETE CASCADE,
              seq INTEGER,
              code_loinc VARCHAR(20),
              code_display TEXT,
              component_value TEXT,
              value_float FLOAT,
              value_unit VARCHAR(30),
              reference_range TEXT,
              interpretation_code VARCHAR(10),
              interpretation_display TEXT
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS lab_result_component_lab_id_idx ON lab_result_component(lab_id)"))
        conn.execute(text("""
            ALTER TABLE clinical_observation
              ADD COLUMN IF NOT EXISTS category_display TEXT,
              ADD COLUMN IF NOT EXISTS issued TIMESTAMP,
              ADD COLUMN IF NOT EXISTS note_text TEXT,
              ADD COLUMN IF NOT EXISTS value_code VARCHAR(100),
              ADD COLUMN IF NOT EXISTS value_display TEXT,
              ADD COLUMN IF NOT EXISTS value_comparator VARCHAR(10)
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS clinical_observation_component (
              id SERIAL PRIMARY KEY,
              observation_id INTEGER NOT NULL REFERENCES clinical_observation(id) ON DELETE CASCADE,
              seq INTEGER,
              code_loinc VARCHAR(20),
              code_display TEXT,
              component_value TEXT,
              value_float FLOAT,
              value_unit VARCHAR(30),
              reference_range TEXT,
              interpretation_code VARCHAR(10),
              interpretation_display TEXT
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS clinical_observation_component_obs_id_idx ON clinical_observation_component(observation_id)"))
        conn.execute(text("""
            ALTER TABLE condition
              ADD COLUMN IF NOT EXISTS category_display TEXT,
              ADD COLUMN IF NOT EXISTS clinical_status_display TEXT,
              ADD COLUMN IF NOT EXISTS verification_status_display TEXT,
              ADD COLUMN IF NOT EXISTS code_text TEXT,
              ADD COLUMN IF NOT EXISTS evidence_refs TEXT
        """))
        conn.execute(text("""
            ALTER TABLE medication_request
              ADD COLUMN IF NOT EXISTS category_display TEXT,
              ADD COLUMN IF NOT EXISTS course_of_therapy_code VARCHAR(100),
              ADD COLUMN IF NOT EXISTS course_of_therapy_display TEXT,
              ADD COLUMN IF NOT EXISTS expected_supply_value FLOAT,
              ADD COLUMN IF NOT EXISTS expected_supply_unit VARCHAR(30),
              ADD COLUMN IF NOT EXISTS quantity_unit VARCHAR(30),
              ADD COLUMN IF NOT EXISTS medication_ref TEXT,
              ADD COLUMN IF NOT EXISTS recorder_ref TEXT,
              ADD COLUMN IF NOT EXISTS recorder_display TEXT,
              ADD COLUMN IF NOT EXISTS reported BOOLEAN,
              ADD COLUMN IF NOT EXISTS prior_prescription_ref TEXT,
              ADD COLUMN IF NOT EXISTS group_identifier_value TEXT,
              ADD COLUMN IF NOT EXISTS substitution_allowed BOOLEAN
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS medication_request_identifier (
              id SERIAL PRIMARY KEY,
              medreq_id TEXT NOT NULL REFERENCES medication_request(fhir_id) ON DELETE CASCADE,
              seq INTEGER,
              system TEXT,
              value TEXT,
              use VARCHAR(20)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS medication_request_identifier_medreq_id_idx ON medication_request_identifier(medreq_id)"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS medication_request_dosage (
              id SERIAL PRIMARY KEY,
              medreq_id TEXT NOT NULL REFERENCES medication_request(fhir_id) ON DELETE CASCADE,
              seq INTEGER,
              dosage_text TEXT,
              route_code VARCHAR(100),
              route_display TEXT,
              method_code VARCHAR(100),
              method_display TEXT,
              patient_instruction TEXT,
              as_needed BOOLEAN,
              timing_text TEXT,
              dose_value FLOAT,
              dose_unit VARCHAR(30)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS medication_request_dosage_medreq_id_idx ON medication_request_dosage(medreq_id)"))
        conn.execute(text("""
            ALTER TABLE medication_statement
              ADD COLUMN IF NOT EXISTS medication_ref TEXT,
              ADD COLUMN IF NOT EXISTS reported BOOLEAN,
              ADD COLUMN IF NOT EXISTS information_source_ref TEXT,
              ADD COLUMN IF NOT EXISTS category_code VARCHAR(100)
        """))
        conn.execute(text("""
            ALTER TABLE immunization
              ADD COLUMN IF NOT EXISTS encounter_id TEXT REFERENCES encounter(id) ON DELETE SET NULL,
              ADD COLUMN IF NOT EXISTS expiration_date DATE,
              ADD COLUMN IF NOT EXISTS location_display TEXT,
              ADD COLUMN IF NOT EXISTS primary_source BOOLEAN,
              ADD COLUMN IF NOT EXISTS report_origin_display TEXT
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS immunization_identifier (
              id SERIAL PRIMARY KEY,
              immunization_id TEXT NOT NULL REFERENCES immunization(fhir_id) ON DELETE CASCADE,
              seq INTEGER,
              system TEXT,
              value TEXT,
              use VARCHAR(20)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS immunization_identifier_immunization_id_idx ON immunization_identifier(immunization_id)"))
        conn.execute(text("""
            ALTER TABLE clinical_note
              ADD COLUMN IF NOT EXISTS type_code VARCHAR(100),
              ADD COLUMN IF NOT EXISTS doc_status VARCHAR(50),
              ADD COLUMN IF NOT EXISTS custodian_display TEXT,
              ADD COLUMN IF NOT EXISTS context_period_start TIMESTAMP,
              ADD COLUMN IF NOT EXISTS context_period_end TIMESTAMP,
              ADD COLUMN IF NOT EXISTS subject_display TEXT,
              ADD COLUMN IF NOT EXISTS author_display TEXT
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS clinical_note_identifier (
              id SERIAL PRIMARY KEY,
              note_id TEXT NOT NULL REFERENCES clinical_note(fhir_id) ON DELETE CASCADE,
              seq INTEGER,
              system TEXT,
              value TEXT,
              use VARCHAR(20)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS clinical_note_identifier_note_id_idx ON clinical_note_identifier(note_id)"))
        conn.execute(text("""
            ALTER TABLE document_reference
              ADD COLUMN IF NOT EXISTS type_code VARCHAR(100),
              ADD COLUMN IF NOT EXISTS doc_status VARCHAR(50),
              ADD COLUMN IF NOT EXISTS authenticator_ref TEXT,
              ADD COLUMN IF NOT EXISTS authenticator_display TEXT,
              ADD COLUMN IF NOT EXISTS custodian_display TEXT,
              ADD COLUMN IF NOT EXISTS context_period_start TIMESTAMP,
              ADD COLUMN IF NOT EXISTS context_period_end TIMESTAMP,
              ADD COLUMN IF NOT EXISTS subject_display TEXT,
              ADD COLUMN IF NOT EXISTS author_display TEXT,
              ADD COLUMN IF NOT EXISTS category_code VARCHAR(100)
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS document_reference_content (
              id SERIAL PRIMARY KEY,
              doc_id TEXT NOT NULL REFERENCES document_reference(fhir_id) ON DELETE CASCADE,
              seq INTEGER,
              format_code VARCHAR(100),
              format_display TEXT,
              attachment_url TEXT,
              attachment_title TEXT,
              attachment_type VARCHAR(100),
              size INTEGER
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS document_reference_content_doc_id_idx ON document_reference_content(doc_id)"))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS document_reference_identifier (
              id SERIAL PRIMARY KEY,
              doc_id TEXT NOT NULL REFERENCES document_reference(fhir_id) ON DELETE CASCADE,
              seq INTEGER,
              system TEXT,
              value TEXT,
              use VARCHAR(20)
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS document_reference_identifier_doc_id_idx ON document_reference_identifier(doc_id)"))
        conn.execute(text("""
            ALTER TABLE allergy_intolerance
              ADD COLUMN IF NOT EXISTS type VARCHAR(50),
              ADD COLUMN IF NOT EXISTS onset_datetime TIMESTAMP,
              ADD COLUMN IF NOT EXISTS code_text TEXT,
              ADD COLUMN IF NOT EXISTS reaction_description TEXT
        """))
        conn.execute(text("""
            ALTER TABLE care_team
              ADD COLUMN IF NOT EXISTS category_code VARCHAR(100)
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS care_team_participant (
              id SERIAL PRIMARY KEY,
              care_team_id TEXT NOT NULL REFERENCES care_team(fhir_id) ON DELETE CASCADE,
              seq INTEGER,
              member_ref TEXT,
              member_display TEXT,
              role_code VARCHAR(100),
              role_display TEXT
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS care_team_participant_care_team_id_idx ON care_team_participant(care_team_id)"))
        conn.execute(text("""
            ALTER TABLE service_request
              ADD COLUMN IF NOT EXISTS priority VARCHAR(10),
              ADD COLUMN IF NOT EXISTS based_on_ref TEXT,
              ADD COLUMN IF NOT EXISTS code_text TEXT
        """))
        conn.execute(text("""
            ALTER TABLE specimen
              ADD COLUMN IF NOT EXISTS identifier_value TEXT
        """))
        conn.execute(text("""
            ALTER TABLE family_member_history
              ADD COLUMN IF NOT EXISTS relationship_code VARCHAR(100)
        """))
        conn.execute(text("""
            ALTER TABLE communication
              ADD COLUMN IF NOT EXISTS category_code VARCHAR(100)
        """))


# ── View Creation ─────────────────────────────────────────────────


def _backfill_fhir_identity_registry(engine, provider: str) -> None:
    """Cache identity displays already present in stored raw FHIR resources."""
    from myhealth_fhir.db.identity import display_from_fhir, ref_from_fhir

    resource_tables = {
        "anthem": ("eob", "claim_submission"),
        "ucla": ("diagnostic_report", "encounter"),
    }[provider]
    with engine.begin() as conn:
        for table in resource_tables:
            for resource_id, raw_json in conn.execute(text(f"SELECT id, raw_json FROM {table} WHERE raw_json IS NOT NULL")):
                try:
                    resource = json.loads(raw_json)
                except (TypeError, ValueError):
                    continue
                actors = []
                if table == "encounter":
                    actors.append((resource.get("subject"), "Patient"))
                    actors.extend(
                        (p.get("individual"), "Practitioner")
                        for p in resource.get("participant", []) if isinstance(p, dict)
                    )
                elif table == "diagnostic_report":
                    actors.extend((p, "Organization") for p in resource.get("performer", []) if isinstance(p, dict))
                    actors.append((resource.get("subject"), "Patient"))
                else:
                    actors.extend(((resource.get("patient"), "Patient"), (resource.get("provider"), "Organization")))
                    actors.append((resource.get("insurer"), "Organization"))
                    payee = resource.get("payee", {}).get("party") if isinstance(resource.get("payee"), dict) else None
                    payee_type = str(payee.get("reference", "")).split("/", 1)[0] if isinstance(payee, dict) and "/" in str(payee.get("reference")) else "Organization"
                    actors.append((payee, payee_type))
                    actors.extend(
                        (c.get("provider"), "Practitioner")
                        for c in resource.get("careTeam", []) if isinstance(c, dict)
                    )
                for actor, fallback_type in actors:
                    if not isinstance(actor, dict):
                        continue
                    ref = ref_from_fhir(actor.get("reference"), provider, fallback_type)
                    name = display_from_fhir(actor)
                    if not ref or not name:
                        continue
                    parts = ref.split(":", 2)
                    conn.execute(text("""
                        INSERT INTO entity_names(entity_ref, provider, entity_type, entity_id, name, display)
                        VALUES (:ref, :provider, :type, :id, :name, :display)
                        ON CONFLICT (entity_ref) DO UPDATE SET
                          name = COALESCE(entity_names.name, EXCLUDED.name),
                          display = COALESCE(entity_names.display, EXCLUDED.display)
                    """), {
                        "ref": ref, "provider": provider, "type": parts[1], "id": parts[2],
                        "name": name, "display": actor.get("display"),
                    })
                if table == "encounter":
                    subject = resource.get("subject")
                    subject_ref = ref_from_fhir(
                        subject.get("reference") if isinstance(subject, dict) else None,
                        provider, "Patient",
                    )
                    if subject_ref:
                        conn.execute(text(
                            "UPDATE encounter SET patient_ref = :ref WHERE id = :id"
                        ), {"ref": subject_ref, "id": resource_id})


def _create_anthem_views(engine):
    """Create denormalized views in the anthem database."""
    with engine.connect() as conn:
        # ── vw_eob — slimmed + reordered: member_submitted, claim_received_date,
        #    payment_type, submission_origin, is_out_of_network added;
        #    claim_type, care_team_roles, billable_period_start/end dropped.
        conn.execute(text("DROP VIEW IF EXISTS vw_eob"))
        conn.execute(text("""
            CREATE VIEW vw_eob AS
            SELECT
              e.claim_number,
              CASE WHEN e.payee_ref LIKE 'anthem:Patient:%'
                   OR en_payee.name ILIKE 'member submitted %'
                   THEN true ELSE false END AS member_submitted,
              split_part(e.patient_ref, ':', 3) AS patient_id,
              en_patient.name AS patient_name,
              en_prov.name AS provider_name,
              COALESCE((SELECT STRING_AGG(DISTINCT en_ct.name, ', ')
                        FROM eob_care_team ct
                        LEFT JOIN entity_names en_ct ON en_ct.entity_ref = ct.provider_ref
                        WHERE ct.eob_id = e.id),
                   '') AS care_team_providers,
              en_payee.name AS payee_name,
              e.status,
              e.outcome,
              e.payment_type,
              e.sub_type,
              e.created_date,
              e.claim_received_date,
              e.payment_date,
              e.payment_amount,
              CASE WHEN e.payee_ref LIKE 'anthem:Patient:%'
                   THEN e.payment_amount END AS paid_to_member,
              CASE WHEN e.payee_ref NOT LIKE 'anthem:Patient:%'
                   THEN e.payment_amount END AS paid_to_provider,
              (SELECT amount FROM eob_total WHERE eob_id = e.id AND category_code = 'submitted' LIMIT 1) AS total_submitted,
              (SELECT amount FROM eob_total WHERE eob_id = e.id AND category_code = 'memberliability' LIMIT 1) AS total_member_liability,
              (SELECT amount FROM eob_total WHERE eob_id = e.id AND category_code = 'benefit' LIMIT 1) AS total_benefit,
              (SELECT amount FROM eob_total WHERE eob_id = e.id AND category_code = 'deductible' LIMIT 1) AS total_deductible,
              (SELECT amount FROM eob_total WHERE eob_id = e.id AND category_code = 'paidtoprovider' LIMIT 1) AS total_paid,
              e.submission_origin,
              e.is_out_of_network,
              i.sequence AS item_seq,
              i.hcpcs_code,
              i.hcpcs_display,
              i.modifier_codes,
              i.serviced_date,
              i.serviced_period_start,
              i.serviced_period_end,
              i.location_code,
              i.location_display,
              i.quantity,
              i.submitted_amount,
              i.net_amount,
              i.allowed_amount,
              i.paid_provider,
              i.paid_patient,
              i.deductible AS item_deductible,
              i.coinsurance AS item_coinsurance,
              i.copay AS item_copay,
              i.noncovered AS item_noncovered,
              i.discount AS item_discount,
              i.member_liability,
              i.payment_status,
              i.adjustment_reason,
              COALESCE((SELECT STRING_AGG(icd_code, ', ') FROM eob_diagnosis WHERE eob_id = e.id), '') AS icd_codes,
              COALESCE((SELECT STRING_AGG(icd_display, ' | ') FROM eob_diagnosis WHERE eob_id = e.id), '') AS icd_displays,
              clm_x.claim_status,
              clm_x.claim_created_date,
              clm_x.claim_total_amount,
              e.last_updated
            FROM eob e
            LEFT JOIN eob_item i ON i.eob_id = e.id
            LEFT JOIN oauth_tokens ot ON ot.patient_id = split_part(e.patient_ref, ':', 3)
            LEFT JOIN patients p ON p.entity_ref = e.patient_ref AND p.provider = ot.provider
            LEFT JOIN entity_names en_patient ON en_patient.entity_ref = p.entity_ref
            LEFT JOIN entity_names en_prov ON en_prov.entity_ref = e.provider_ref
            LEFT JOIN entity_names en_payee ON en_payee.entity_ref = e.payee_ref
            LEFT JOIN LATERAL (
                SELECT c.status AS claim_status,
                       c.created_date AS claim_created_date,
                       c.total_amount AS claim_total_amount
                FROM claim_submission c
                WHERE c.claim_number = e.claim_number
                ORDER BY (c.status = 'cancelled') ASC, c.created_date DESC
                LIMIT 1
            ) clm_x ON true
        """))

        # ── vw_claims — slimmed + reordered: member_submitted, claim_received_date,
        #    submission_origin, is_out_of_network, payee_type, adjudication_status_code,
        #    action_date, action_type_code, adjustment_number, payee_name added;
        #    use, priority, total_currency, discharge_status_code,
        #    network_identifier_code, claim_type, care_team_roles dropped.
        conn.execute(text("DROP VIEW IF EXISTS vw_claims"))
        conn.execute(text("""
            CREATE VIEW vw_claims AS
            SELECT
              c.claim_number,
              c.claim_adjustment_key,
              CASE WHEN c.payee_ref LIKE 'anthem:Patient:%'
                   OR en_payee.name ILIKE 'member submitted %'
                   THEN true ELSE false END AS member_submitted,
              split_part(c.patient_ref, ':', 3) AS patient_id,
              en_patient.name AS patient_name,
              en_prov.name AS provider_name,
              COALESCE((SELECT STRING_AGG(DISTINCT en_ct.name, ', ')
                        FROM claim_care_team ct
                        LEFT JOIN entity_names en_ct ON en_ct.entity_ref = ct.provider_ref
                        WHERE ct.claim_id = c.id),
                   '') AS care_team_providers,
              en_payee.name AS payee_name,
              en_ins.name AS insurer_name,
              c.status,
              c.line_status_display,
              c.denial_reason_code,
              c.adjudication_status_code,
              c.action_type_code,
              c.created_date,
              (SELECT MIN(e2.claim_received_date) FROM eob e2 WHERE e2.claim_number = c.claim_number) AS claim_received_date,
              c.adjudication_date,
              c.action_date,
              c.paid_date,
              c.billable_period_start,
              c.billable_period_end,
              c.total_amount,
              c.payee_type,
              c.submission_origin,
              c.is_out_of_network,
              c.adjustment_number,
              c.document_control_number,
              i.sequence AS item_seq,
              i.quantity,
              i.unit_price,
              i.net_amount,
              i.hcpcs_code,
              i.hcpcs_display,
              i.modifier_codes,
              i.serviced_date,
              i.serviced_period_start,
              i.serviced_period_end,
              i.location_code,
              i.location_display,
              COALESCE((SELECT STRING_AGG(icd_code, ', ') FROM claim_diagnosis WHERE claim_id = c.id), '') AS icd_codes,
              COALESCE((SELECT STRING_AGG(icd_display, ' | ') FROM claim_diagnosis WHERE claim_id = c.id), '') AS icd_displays,
              eob_x.eob_status,
              eob_x.eob_outcome,
              eob_x.eob_disposition,
              eob_x.eob_payment_amount,
              eob_x.eob_created_date,
              c.last_updated
            FROM claim_submission c
            LEFT JOIN claim_item i ON i.claim_id = c.id
            LEFT JOIN oauth_tokens ot ON ot.patient_id = split_part(c.patient_ref, ':', 3)
            LEFT JOIN patients p ON p.entity_ref = c.patient_ref AND p.provider = ot.provider
            LEFT JOIN entity_names en_patient ON en_patient.entity_ref = p.entity_ref
            LEFT JOIN entity_names en_prov ON en_prov.entity_ref = c.provider_ref
            LEFT JOIN entity_names en_ins ON en_ins.entity_ref = c.insurer_ref
            LEFT JOIN entity_names en_payee ON en_payee.entity_ref = c.payee_ref
            LEFT JOIN LATERAL (
                SELECT e.status AS eob_status,
                       e.outcome AS eob_outcome,
                       e.disposition AS eob_disposition,
                       e.payment_amount AS eob_payment_amount,
                       e.created_date AS eob_created_date
                FROM eob e
                WHERE e.claim_number = c.claim_number
                ORDER BY (e.status = 'cancelled') ASC, e.created_date DESC
                LIMIT 1
            ) eob_x ON true
        """))

        conn.commit()


def _create_ucla_views(engine):
    """Create denormalized views in the ucla database."""
    with engine.connect() as conn:
        # lab_results view (no cross-DB join — patient_name resolved at app layer)
        conn.execute(text("DROP VIEW IF EXISTS lab_results"))
        conn.execute(text("""
            CREATE VIEW lab_results AS
            SELECT
              dr.id AS panel_id,
              dr.patient_id,
              dr.provider,
              dr.status AS panel_status,
              dr.code_display AS panel_name,
              dr.code_loinc AS panel_loinc,
              dr.effective_datetime AS panel_date,
                en_performer.name AS lab_name,
              lr.id AS result_id,
              lr.fhir_id AS observation_id,
              lr.code_display AS test_name,
              lr.code_text AS test_short,
              lr.code_loinc AS test_loinc,
              lr.value AS result_value,
              lr.value_float AS result_numeric,
              lr.value_unit,
              lr.reference_range,
              lr.interpretation_code,
              lr.interpretation_display,
              lr.effective_datetime AS result_date,
              lr.status AS result_status,
              dr.loaded_at AS panel_loaded_at,
              lr.loaded_at AS result_loaded_at
             FROM diagnostic_report dr
             JOIN lab_result lr ON lr.report_id = dr.id
             LEFT JOIN entity_names en_performer ON en_performer.entity_ref = dr.performer_ref
        """))

        # clinical_overview view — join encounters with observation counts
        conn.execute(text("DROP VIEW IF EXISTS clinical_overview"))
        conn.execute(text("""
            CREATE VIEW clinical_overview AS
            SELECT
              e.id AS encounter_id,
              e.patient_id,
               en_patient.name AS patient_name,
              e.status AS encounter_status,
              e.class_ AS encounter_class,
              e.period_start,
              e.period_end,
              e.reason_display,
              e.location,
              e.source AS encounter_source,
              COUNT(DISTINCT dr.id) AS lab_panels_count,
              COUNT(DISTINCT lr.fhir_id) AS lab_results_count,
              COUNT(DISTINCT co.fhir_id) AS clinical_observations_count,
              COUNT(DISTINCT cn.fhir_id) AS notes_count,
              COUNT(DISTINCT ma.fhir_id) AS medication_admin_count,
              COUNT(DISTINCT sr.fhir_id) AS service_requests_count,
              MIN(COALESCE(dr.effective_datetime, co.effective_datetime, cn.authored_datetime)) AS earliest_result_date,
              MAX(COALESCE(dr.effective_datetime, co.effective_datetime, cn.authored_datetime)) AS latest_result_date
             FROM encounter e
             LEFT JOIN entity_names en_patient ON en_patient.entity_ref = e.patient_ref
            LEFT JOIN diagnostic_report dr ON dr.encounter_id = e.id
            LEFT JOIN lab_result lr ON lr.report_id = dr.id
            LEFT JOIN clinical_observation co ON co.encounter_id = e.id
            LEFT JOIN clinical_note cn ON cn.encounter_id = e.id
            LEFT JOIN medication_administration ma ON ma.encounter_id = e.id
            LEFT JOIN service_request sr ON sr.encounter_id = e.id
            GROUP BY e.id, en_patient.name
        """))

        conn.commit()


def _sync_oauth_tokens_replica():
    """Sync oauth_tokens and patients from the auth DB to anthem and ucla replicas for view joins."""
    with get_auth_session() as auth_session:
        token_records = auth_session.query(AuthOAuthTokenRecord).all()
        patient_records = auth_session.query(AuthPatientRecord).all()
        entity_records = auth_session.query(AuthEntityName).all()
    if not token_records:
        return

    # Sync to anthem DB
    with get_anthem_session() as anthem_session:
        for record in token_records:
            anthem_session.merge(
                AnthemOAuthTokenRecord(
                    patient_id=record.patient_id,
                    provider=record.provider,
                    access_token=record.access_token,
                    token_type=record.token_type,
                    expires_in=record.expires_in,
                    scope=record.scope,
                    refresh_token=record.refresh_token,
                    id_token=record.id_token,
                    obtained_at=record.obtained_at,
                    refresh_token_expires_in=record.refresh_token_expires_in,
                    last_eob_fetch=record.last_eob_fetch,
                    last_claim_fetch=record.last_claim_fetch,
                )
            )
        for record in patient_records:
            anthem_session.merge(
                AnthemPatientRecord(
                    patient_id=record.patient_id,
                    provider=record.provider,
                    entity_ref=record.entity_ref,
                )
            )
        for record in entity_records:
            target = anthem_session.get(EntityName, record.entity_ref)
            if target is None:
                target = EntityName(
                    entity_ref=record.entity_ref, provider=record.provider,
                    entity_type=record.entity_type, entity_id=record.entity_id,
                )
                anthem_session.add(target)
            if record.name:
                target.name = record.name
            if record.npi:
                target.npi = record.npi
            if record.display:
                target.display = record.display
        anthem_session.commit()

    # Sync to ucla DB
    with get_ucla_session() as ucla_session:
        for record in token_records:
            ucla_session.merge(
                UclaOAuthTokenRecord(
                    patient_id=record.patient_id,
                    provider=record.provider,
                    access_token=record.access_token,
                    token_type=record.token_type,
                    expires_in=record.expires_in,
                    scope=record.scope,
                    refresh_token=record.refresh_token,
                    id_token=record.id_token,
                    obtained_at=record.obtained_at,
                    refresh_token_expires_in=record.refresh_token_expires_in,
                    last_eob_fetch=record.last_eob_fetch,
                    last_claim_fetch=record.last_claim_fetch,
                )
            )
        for record in patient_records:
            ucla_session.merge(
                UclaPatientRecord(
                    patient_id=record.patient_id,
                    provider=record.provider,
                    entity_ref=record.entity_ref,
                )
            )
        for record in entity_records:
            target = ucla_session.get(UclaEntityName, record.entity_ref)
            if target is None:
                target = UclaEntityName(
                    entity_ref=record.entity_ref, provider=record.provider,
                    entity_type=record.entity_type, entity_id=record.entity_id,
                )
                ucla_session.add(target)
            if record.name:
                target.name = record.name
            if record.npi:
                target.npi = record.npi
            if record.display:
                target.display = record.display
        ucla_session.commit()

"""One-time-per-process schema initialization (bootstrap).

``init_db()`` is idempotent within a process; the heavy lifting (migrations,
view DDL, replica sync) is delegated to ``db.schema.migrations``,
``db.schema.views`` and ``services.oauth_sync``.
"""

import threading

from sqlalchemy import text

from myhealth_fhir.db.engine import (
    get_anthem_engine,
    get_auth_engine,
    get_ucla_engine,
    is_postgres,
)
from myhealth_fhir.db.schema.migrations import (
    _backfill_auth_patient_registry,
    _backfill_fhir_identity_registry,
    _migrate_anthem_schema,
    _migrate_auth_schema,
    _migrate_raw_json_unpack_anthem,
    _migrate_raw_json_unpack_ucla,
    _migrate_ucla_schema,
)
from myhealth_fhir.db.schema.views import _create_anthem_views, _create_ucla_views
from myhealth_fhir.models.anthem import AnthemBase
from myhealth_fhir.models.auth import AuthBase
from myhealth_fhir.models.ucla import UclaBase
from myhealth_fhir.services.oauth_sync import sync_oauth_tokens_replica

_initialized = False
_init_lock = threading.RLock()


def init_db():
    """Create all tables and views in all three databases."""
    global _initialized
    with _init_lock:
        if _initialized:
            return
        _init_db_once()
        _initialized = True


def _init_db_once():
    """Perform one process-wide schema initialization pass.

    Schema (tables + views) is owned by Alembic (``myhealth_fhir.db.alembic``):
    on PostgreSQL we run ``alembic upgrade head`` per database, which builds the
    full baseline on a fresh DB and is a no-op on an already-stamped one. The
    imperative ``create_all`` is retained only for the SQLite dev fallback
    (the baseline is PostgreSQL-typed). The data backfills and OAuth replica
    sync below are idempotent and remain for backward-compat safety.
    """
    from myhealth_fhir.db.alembic import run_all

    auth_engine = get_auth_engine()
    anthem_engine = get_anthem_engine()
    ucla_engine = get_ucla_engine()

    if is_postgres():
        # Schema via Alembic (baseline = current shape; stamped live DBs no-op).
        run_all()
        with auth_engine.begin() as conn:
            conn.execute(
                text(
                    "DROP VIEW IF EXISTS "
                    "member_claims_summary, member_claims_recon, claim_items, "
                    "claim_submissions, eob_items, eob_claims CASCADE"
                )
            )
        with anthem_engine.begin() as conn:
            conn.execute(
                text(
                    "DROP VIEW IF EXISTS member_claims, member_claims_summary, "
                    "member_claims_recon, claim_items, claim_submissions, "
                    "eob_items, eob_claims, vw_eob_all, vw_claims_all CASCADE"
                )
            )
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
    else:
        # SQLite dev fallback: the Alembic baseline is PostgreSQL-typed, so the
        # dev SQLite path keeps the imperative create_all.
        AuthBase.metadata.create_all(auth_engine)
        AnthemBase.metadata.create_all(anthem_engine)
        UclaBase.metadata.create_all(ucla_engine)

    _create_anthem_views(anthem_engine)
    _create_ucla_views(ucla_engine)

    # Sync oauth_tokens replicas
    sync_oauth_tokens_replica()

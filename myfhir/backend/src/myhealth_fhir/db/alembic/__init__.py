"""Alembic migration runner — one script-location per database.

Databases: ``auth``, ``anthem``, ``ucla``. Each has its own ``alembic_version``
table in its own DB and its own ``versions/`` chain.

Baseline was created by **stamping** the live DBs (``alembic stamp head``) after
autogenerating a baseline revision from the ORM models against an empty scratch
DB — the baseline DDL is therefore the source of truth for *fresh* databases,
while existing databases are marked at head without re-running it.

Programmatic entry point (called by ``db.schema.bootstrap.init_db``)::

    from myhealth_fhir.db.alembic import run_all
    run_all()
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

_HERE = Path(__file__).resolve().parent

#: provider key -> physical database name
DBS: dict[str, str] = {
    "auth": "myhealth_auth",
    "anthem": "myhealth_anthem",
    "ucla": "myhealth_ucla",
}


def _resolve_url(db: str) -> str:
    from myhealth_fhir.db.engine import _resolve_db_url

    return _resolve_db_url(DBS[db])


def _make_config(db: str, url: str | None = None) -> Config:
    cfg = Config()
    cfg.config_file_name = None  # fully programmatic; no alembic.ini on disk
    cfg.set_main_option("script_location", str(_HERE / db))
    cfg.set_main_option("sqlalchemy.url", url or _resolve_url(db))
    return cfg


def preflight(db: str) -> None:
    """Fail closed if the DB has tables but no ``alembic_version``.

    Guards the "stamping forgotten on a fresh environment" failure mode: if the
    pre-Alembic imperative ``init_db`` created tables without a stamp, we refuse
    to auto-run the baseline (which would hit ``already exists``) and instead ask
    for an explicit ``alembic stamp`` decision.
    """
    engine = create_engine(_resolve_url(db))
    try:
        insp = inspect(engine)
        tables = insp.get_table_names()
    finally:
        engine.dispose()
    has_version = "alembic_version" in tables
    if tables and not has_version:
        raise RuntimeError(
            f"Alembic preflight failed for '{db}' ({DBS[db]}): {len(tables)} tables exist "
            f"but there is no alembic_version table. This looks like the pre-Alembic "
            f"imperative init_db ran without a stamp. Decide explicitly and stamp: "
            f"'alembic stamp head' (schema matches current) or 'alembic stamp base' "
            f"(re-run the baseline), then retry."
        )


def upgrade(db: str, target: str = "head") -> None:
    """Preflight then run ``alembic upgrade`` to *target* for one database."""
    preflight(db)
    command.upgrade(_make_config(db), target)


def stamp(db: str, revision: str = "head") -> None:
    """Record a revision as applied without running its DDL (baseline-by-stamp)."""
    command.stamp(_make_config(db), revision)


def current(db: str) -> str | None:
    """Return the DB's current revision identifier, or None if unversioned."""
    engine = create_engine(_resolve_url(db))
    try:
        with engine.connect() as conn:
            try:
                row = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
                return row.scalar_one()
            except Exception:
                return None
    finally:
        engine.dispose()


def run_all(dbs: Iterable[str] | None = None) -> None:
    """Run ``alembic upgrade head`` for each database (default: all three)."""
    for db in dbs or list(DBS):
        upgrade(db, "head")

"""Engine and session management for the three PostgreSQL databases.

Databases:
  - myhealth_anthem  : Insurance data (EOB, claims, members, entities)
  - myhealth_ucla    : Clinical data (labs, imaging, encounters, notes)
  - myhealth_auth    : OAuth tokens (primary source)

Usage:
  from myhealth_fhir.db.engine import get_anthem_session, get_ucla_session, get_auth_session

  with get_anthem_session() as session:
      eobs = session.query(EOB).all()
"""

import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


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

_engines: dict[str, Any] = {}


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


def is_postgres() -> bool:
    url = _resolve_db_url("myhealth_auth")
    return url.startswith("postgresql")

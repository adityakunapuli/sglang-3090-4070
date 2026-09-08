"""Auth database models — OAuth tokens (primary source) and patient identity."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class AuthBase(DeclarativeBase):
    """SQLAlchemy declarative base for auth database models."""


class OAuthTokenRecord(AuthBase):
    """Persisted OAuth2 token row, keyed by (patient_id, provider).

    This is the PRIMARY source of truth for tokens. The same table
    is replicated into anthem and ucla databases for view joins.
    """

    __tablename__ = "oauth_tokens"

    patient_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), primary_key=True)
    access_token: Mapped[str] = mapped_column(String(2000))
    token_type: Mapped[str] = mapped_column(String(20), default="Bearer")
    expires_in: Mapped[int] = mapped_column(Integer, default=3600)
    scope: Mapped[str | None] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text)
    id_token: Mapped[str | None] = mapped_column(Text)
    obtained_at: Mapped[datetime] = mapped_column(DateTime)
    refresh_token_expires_in: Mapped[int] = mapped_column(Integer, default=2592000)
    last_eob_fetch: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_claim_fetch: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class EntityName(AuthBase):
    """Normalized identity registry for the auth database."""

    __tablename__ = "entity_names"

    entity_ref: Mapped[str] = mapped_column(String(255), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    npi: Mapped[str | None] = mapped_column(String(20))
    display: Mapped[str | None] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PatientRecord(AuthBase):
    """Patient identity mapping separate from token storage."""

    __tablename__ = "patients"

    patient_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), primary_key=True)
    entity_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class PKCEVerifier(AuthBase):
    """Persisted PKCE code_verifier, keyed by provider.

    Created when an authorize URL is built (before any token exists, so it
    cannot live on ``oauth_tokens``), then consumed and cleared by the token
    exchange. Stored in Postgres so the flow survives restarts and works
    across process boundaries (CLI, dashboard API, uvicorn workers).
    """

    __tablename__ = "pkce_verifiers"

    provider: Mapped[str] = mapped_column(String(20), primary_key=True)
    verifier: Mapped[str] = mapped_column(String(200))
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class JobRun(AuthBase):
    """Per-run summary of the data-pull job, written by the job daemon."""

    __tablename__ = "job_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(20), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|success|partial|failed
    counts: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

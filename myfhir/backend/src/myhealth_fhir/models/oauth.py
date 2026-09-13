"""OAuth2 token model and DB-backed TokenStore (multi-patient)."""


import json
import logging
import os
from typing import Any, Self
from datetime import UTC, datetime

from myhealth_fhir.db import get_auth_session, init_db
from myhealth_fhir.db.identity import upsert_patient_name

log = logging.getLogger("myhealth_fhir.oauth")


# ── OAuthToken data class ──────────────────────────────────────────


class OAuthToken:
    """In-memory representation of an OAuth2 access + refresh token pair."""

    def __init__(
        self,
        access_token: str,
        token_type: str = "Bearer",
        expires_in: int = 3600,
        scope: str = "",
        refresh_token: str | None = None,
        id_token: str | None = None,
        obtained_at: datetime | None = None,
        refresh_token_expires_in: int = 2592000,
        patient_name: str | None = None,
        storage_patient_id: str | None = None,
    ):
        """Initialize the token with access/refresh values and an obtained timestamp."""
        self.access_token = access_token
        self.token_type = token_type
        self.expires_in = int(expires_in) if expires_in is not None else 3600
        self.scope = scope
        self.refresh_token = refresh_token
        self.id_token = id_token
        self.obtained_at = obtained_at or datetime.now(UTC)
        self.refresh_token_expires_in = (
            int(refresh_token_expires_in) if refresh_token_expires_in is not None else 2592000
        )
        self.patient_name = patient_name
        self.storage_patient_id = storage_patient_id

    @property
    def expires_at(self) -> datetime:
        """Return the absolute expiry timestamp of the access token."""
        from datetime import timedelta

        return self.obtained_at + timedelta(seconds=self.expires_in)

    @property
    def seconds_remaining(self) -> float:
        """Return seconds remaining before the access token expires (>= 0)."""
        delta = self.expires_at - datetime.now(UTC)
        return max(0, delta.total_seconds())

    @property
    def expired(self) -> bool:
        """Return True if the access token has expired."""
        return self.seconds_remaining <= 0

    @property
    def refresh_expired(self) -> bool:
        """Return True if the refresh token has expired."""
        return (datetime.now(UTC) - self.obtained_at).total_seconds() > self.refresh_token_expires_in

    def approach_expiry(self, threshold: int = 300) -> bool:
        """Return True if the access token will expire within `threshold` seconds."""
        return self.seconds_remaining <= threshold and not self.expired

    def decode_id_token(self) -> dict | None:
        """Decode the JWT id_token payload (without signature verification)."""
        if not self.id_token:
            return None
        try:
            import base64

            payload = self.id_token.split(".")[1]
            decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
            return json.loads(decoded)
        except Exception:
            return None

    @property
    def patient_id(self) -> str | None:
        """Return the patient id from the id_token `sub` claim, if present."""
        data = self.decode_id_token()
        return data.get("sub") if data else None

    def to_dict(self) -> dict:
        """Serialize the token to a JSON-friendly dict (for legacy migration)."""
        return {
            "access_token": self.access_token,
            "token_type": self.token_type,
            "expires_in": self.expires_in,
            "scope": self.scope,
            "refresh_token": self.refresh_token,
            "id_token": self.id_token,
            "obtained_at": self.obtained_at.isoformat(),
            "refresh_token_expires_in": self.refresh_token_expires_in,
            "patient_name": self.patient_name,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        """Construct an OAuthToken from a legacy dict (as written by old JSON storage)."""
        if isinstance(data.get("obtained_at"), str):
            data["obtained_at"] = datetime.fromisoformat(data["obtained_at"])
        return cls(**data)

    @classmethod
    def from_record(cls, record: Any, patient_name: str | None = None) -> Self:
        """Construct an OAuthToken from an OAuthTokenRecord ORM row."""
        obtained = record.obtained_at
        if obtained is not None and obtained.tzinfo is None:
            obtained = obtained.replace(tzinfo=UTC)
        return cls(
            access_token=record.access_token,
            token_type=record.token_type,
            expires_in=record.expires_in,
            scope=record.scope or "",
            refresh_token=record.refresh_token,
            id_token=record.id_token,
            obtained_at=obtained,
            refresh_token_expires_in=record.refresh_token_expires_in,
            patient_name=patient_name,
            storage_patient_id=record.patient_id,
        )


# ── Legacy JSON token path (for migration) ────────────────────────


def legacy_token_path(provider: str = "anthem") -> str:
    """Return the legacy per-provider JSON token file path (`.auth/<provider>/token.json`)."""
    return f".auth/{provider}/token.json"


# ── DB-backed TokenStore (multi-patient) ──────────────────────────


class TokenStore:
    """DB-backed multi-patient token store with a one-time JSON migration."""

    def __init__(self, filepath: str | None = None, provider: str = "anthem"):
        """Initialize the store for a provider and run the legacy JSON migration."""
        self.filepath = filepath or legacy_token_path(provider)
        self.provider = provider
        init_db()
        self.migrate_legacy()

    def migrate_legacy(self) -> None:
        """Migrate a legacy `.auth/<provider>/token.json` file into the DB, then remove it."""
        if not os.path.exists(self.filepath):
            return
        log.info("Migrating legacy token file %s to database", self.filepath)
        try:
            with open(self.filepath) as f:
                data = json.load(f)
            token = OAuthToken.from_dict(data)
            self.save(token)
            os.remove(self.filepath)
            tmp = self.filepath + ".tmp"
            if os.path.exists(tmp):
                os.remove(tmp)
            log.info("Legacy token migrated to database")
        except Exception as e:
            log.warning("Failed to migrate legacy token: %s", e)

    def pid(self, token: OAuthToken) -> str:
        """Return the patient id for a token, defaulting to `_default`."""
        return token.patient_id or "_default"

    def save(self, token: OAuthToken) -> None:
        """Upsert a token row keyed by (patient_id, provider)."""
        from myhealth_fhir.db.models_auth import OAuthTokenRecord, PatientRecord

        patient_id = self.pid(token)
        with get_auth_session() as session:
            record = session.get(OAuthTokenRecord, (patient_id, self.provider))
            if record is None:
                record = OAuthTokenRecord(
                    patient_id=patient_id,
                    provider=self.provider,
                )
            record.access_token = token.access_token
            record.token_type = token.token_type
            record.expires_in = token.expires_in
            record.scope = token.scope
            record.refresh_token = token.refresh_token
            record.id_token = token.id_token
            obtained = token.obtained_at
            if obtained is not None and obtained.tzinfo is not None:
                obtained = obtained.astimezone(UTC).replace(tzinfo=None)
            record.obtained_at = obtained
            record.refresh_token_expires_in = token.refresh_token_expires_in
            session.merge(record)

            # Save patient identity separately from the token row.
            if token.patient_name:
                patient_record = session.get(PatientRecord, (patient_id, self.provider))
                if patient_record is None:
                    patient_record = PatientRecord(
                        patient_id=patient_id,
                        provider=self.provider,
                    )
                patient_record.entity_ref = upsert_patient_name(
                    session, provider=self.provider, patient_id=patient_id, name=token.patient_name
                )
                session.merge(patient_record)

            session.commit()

        # Sync replica to anthem/ucla DBs
        from myhealth_fhir.services.oauth_sync import sync_oauth_tokens_replica
        sync_oauth_tokens_replica()

    def load(self, patient_id: str | None = None) -> OAuthToken | None:
        """Load a token for a patient, or the most-recently-updated one if no id given."""
        from myhealth_fhir.db.models_auth import OAuthTokenRecord, PatientRecord

        with get_auth_session() as session:
            if patient_id:
                record = session.get(OAuthTokenRecord, (patient_id, self.provider))
            else:
                record = (
                    session.query(OAuthTokenRecord)
                    .filter(OAuthTokenRecord.provider == self.provider)
                    .order_by(OAuthTokenRecord.updated_at.desc())
                    .first()
                )
            if record is None:
                return None
            # Resolve the display name from the normalized registry.
            patient_record = session.get(PatientRecord, (record.patient_id, self.provider))
            patient_name = None
            if patient_record and patient_record.entity_ref:
                from myhealth_fhir.db.models_auth import EntityName

                entity = session.get(EntityName, patient_record.entity_ref)
                patient_name = entity.name if entity else None
            return OAuthToken.from_record(record, patient_name)

    def list_patient_ids(self) -> list[str]:
        """Return all stored patient ids for this provider (excluding `_default`)."""
        from myhealth_fhir.db.models_auth import OAuthTokenRecord

        with get_auth_session() as session:
            rows = session.query(OAuthTokenRecord.patient_id).filter(OAuthTokenRecord.provider == self.provider).all()
            return [r[0] for r in rows if r[0] != "_default"]

    def list_all_patient_ids(self) -> list[str]:
        """Return every stored token key, including the legacy `_default` key."""
        from myhealth_fhir.db.models_auth import OAuthTokenRecord

        with get_auth_session() as session:
            rows = session.query(OAuthTokenRecord.patient_id).filter(OAuthTokenRecord.provider == self.provider).all()
            return [r[0] for r in rows]

    def list_tokens(self) -> list[OAuthToken]:
        """Return all stored tokens for this provider as OAuthToken instances."""
        from myhealth_fhir.db.models_auth import EntityName, OAuthTokenRecord, PatientRecord

        with get_auth_session() as session:
            records = session.query(OAuthTokenRecord).filter(OAuthTokenRecord.provider == self.provider).all()
            # Preload normalized patient names.
            patient_names = {}
            for record in records:
                patient_record = session.get(PatientRecord, (record.patient_id, self.provider))
                entity = session.get(EntityName, patient_record.entity_ref) if patient_record and patient_record.entity_ref else None
                patient_names[record.patient_id] = entity.name if entity else None
            return [OAuthToken.from_record(r, patient_names.get(r.patient_id)) for r in records]

    def clear(self, patient_id: str | None = None) -> None:
        """Delete stored tokens for one patient or all patients for this provider."""
        from myhealth_fhir.db.models_auth import OAuthTokenRecord

        with get_auth_session() as session:
            q = session.query(OAuthTokenRecord).filter(OAuthTokenRecord.provider == self.provider)
            if patient_id:
                q = q.filter(OAuthTokenRecord.patient_id == patient_id)
            q.delete()
            session.commit()

    def count(self) -> int:
        """Return the number of stored tokens for this provider."""
        from myhealth_fhir.db.models_auth import OAuthTokenRecord

        with get_auth_session() as session:
            return session.query(OAuthTokenRecord).filter(OAuthTokenRecord.provider == self.provider).count()

    def get_last_eob_fetch(self, patient_id: str) -> datetime | None:
        """Return the last EOB fetch timestamp for a patient, or None."""
        from myhealth_fhir.db.models_auth import OAuthTokenRecord

        with get_auth_session() as session:
            record = session.get(OAuthTokenRecord, (patient_id, self.provider))
            if record is None:
                return None
            val = record.last_eob_fetch
            if val is not None and val.tzinfo is None:
                val = val.replace(tzinfo=UTC)
            return val

    def set_last_eob_fetch(self, patient_id: str, dt: datetime | None = None) -> None:
        """Record the last EOB fetch timestamp for a patient (defaults to now)."""
        from myhealth_fhir.db.models_auth import OAuthTokenRecord

        if dt is None:
            dt = datetime.now(UTC)
        with get_auth_session() as session:
            record = session.get(OAuthTokenRecord, (patient_id, self.provider))
            if record is not None:
                if dt.tzinfo is not None:
                    dt = dt.astimezone(UTC).replace(tzinfo=None)
                record.last_eob_fetch = dt
                session.commit()

    def get_last_claim_fetch(self, patient_id: str) -> datetime | None:
        """Return the last Claim fetch timestamp for a patient, or None."""
        from myhealth_fhir.db.models_auth import OAuthTokenRecord

        with get_auth_session() as session:
            record = session.get(OAuthTokenRecord, (patient_id, self.provider))
            if record is None:
                return None
            val = record.last_claim_fetch
            if val is not None and val.tzinfo is None:
                val = val.replace(tzinfo=UTC)
            return val

    def set_last_claim_fetch(self, patient_id: str, dt: datetime | None = None) -> None:
        """Record the last Claim fetch timestamp for a patient (defaults to now)."""
        from myhealth_fhir.db.models_auth import OAuthTokenRecord

        if dt is None:
            dt = datetime.now(UTC)
        with get_auth_session() as session:
            record = session.get(OAuthTokenRecord, (patient_id, self.provider))
            if record is not None:
                if dt.tzinfo is not None:
                    dt = dt.astimezone(UTC).replace(tzinfo=None)
                record.last_claim_fetch = dt
                session.commit()

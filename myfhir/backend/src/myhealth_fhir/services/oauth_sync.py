"""OAuth token replica sync: auth DB -> anthem/ucla replica tables.

Cross-database writer (kept out of db/ on purpose: it is a sync *service*,
not db infrastructure). Called once from ``db.schema.bootstrap`` during
``init_db`` and after token saves from ``services.oauth.TokenStore``.
"""

from myhealth_fhir.db.engine import get_anthem_session, get_auth_session, get_ucla_session
from myhealth_fhir.db.models_anthem import (
    EntityName,
    OAuthTokenRecord as AnthemOAuthTokenRecord,
    PatientRecord as AnthemPatientRecord,
)
from myhealth_fhir.db.models_auth import (
    EntityName as AuthEntityName,
    OAuthTokenRecord as AuthOAuthTokenRecord,
    PatientRecord as AuthPatientRecord,
)
from myhealth_fhir.db.models_ucla import (
    EntityName as UclaEntityName,
    OAuthTokenRecord as UclaOAuthTokenRecord,
    PatientRecord as UclaPatientRecord,
)


def sync_oauth_tokens_replica():
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

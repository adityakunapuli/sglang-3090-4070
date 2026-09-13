"""Anthem EOB/Claim persistence to the anthem DB.

Module functions converted from the former ``FHIRClient.save_eobs_to_db`` /
``save_claims_to_db`` methods (plan 4b). ``client`` is the FHIRClient
instance; only its public ``get_patient`` / ``extract_patient_name`` and
``_cache_patient_identity`` are used.
"""

import logging

log = logging.getLogger(__name__)


def _cache_patient_identity(client, patient_id: str) -> None:
    """Fetch and persist an Anthem patient identity when the registry lacks it."""
    from myhealth_fhir.db import get_anthem_session
    from myhealth_fhir.db.identity import upsert_patient_name
    from myhealth_fhir.models.anthem import PatientRecord

    try:
        patient = client.get_patient(patient_id)
        name = client.extract_patient_name(patient)
        if not name or name == patient_id:
            return
        with get_anthem_session() as session:
            ref = upsert_patient_name(session, provider="anthem", patient_id=patient_id, name=name)
            record = session.get(PatientRecord, (patient_id, "anthem"))
            if record is None:
                record = PatientRecord(patient_id=patient_id, provider="anthem")
            record.entity_ref = ref
            session.merge(record)
            session.commit()
    except Exception:
        log.debug("Unable to cache patient identity", exc_info=True)


def save_eobs_to_db(client, eobs: list[dict]) -> int:
    """Write EOB dicts to the anthem DB. Handles upserts by delete-and-reinsert."""
    from myhealth_fhir.db import get_anthem_session
    from myhealth_fhir.db.parser import (
        load_care_team,
        load_diagnoses,
        load_eob,
        load_eob_adjudications,
        load_eob_identifiers,
        load_eob_procedures,
        load_eob_supporting_info,
        load_item,
        load_item_adjudications,
        load_totals,
    )
    from myhealth_fhir.models.anthem import (
        EOB,
        EOBAdjudication,
        EOBCareTeam,
        EOBDiagnosis,
        EOBIdentifier,
        EOBItem,
        EOBItemAdjudication,
        EOBProcedure,
        EOBSupportingInfo,
        EOBTotal,
    )

    count = 0
    patient_ids = {
        rec.get("patient", {}).get("reference", "").split("/", 1)[-1]
        for rec in eobs if isinstance(rec.get("patient"), dict) and rec["patient"].get("reference")
    }
    for patient_id in patient_ids:
        _cache_patient_identity(client, patient_id)
    with get_anthem_session() as session:
        for rec in eobs:
            eob_id = rec["id"]
            existing = session.get(EOB, eob_id)
            if existing:
                session.delete(existing)
                session.flush()
            eob = EOB(**load_eob(rec))
            session.add(eob)
            session.flush()

            for item_rec in rec.get("item", []):
                item = EOBItem(**load_item(item_rec, eob_id))
                session.add(item)
                session.flush()
                for adj in load_item_adjudications(item_rec, item.id):
                    session.add(EOBItemAdjudication(**adj))

            for dx in load_diagnoses(rec, eob_id):
                session.add(EOBDiagnosis(**dx))

            for ct in load_care_team(rec, eob_id):
                session.add(EOBCareTeam(**ct))

            for t in load_totals(rec, eob_id):
                session.add(EOBTotal(**t))

            for ident in load_eob_identifiers(rec, eob_id):
                session.add(EOBIdentifier(**ident))

            for adj in load_eob_adjudications(rec, eob_id):
                session.add(EOBAdjudication(**adj))

            for si in load_eob_supporting_info(rec, eob_id):
                session.add(EOBSupportingInfo(**si))

            for proc in load_eob_procedures(rec, eob_id):
                session.add(EOBProcedure(**proc))

            count += 1
            if count % 20 == 0:
                session.flush()

        session.commit()
    try:
        from myhealth_fhir.services.member_submissions import match_registered_submissions
        matched = match_registered_submissions(eobs=eobs, claims=[])
        if matched:
            log.info("Matched %d registered member submissions to EOBs", matched)
    except Exception:
        log.exception("Member-submission matching failed after EOB save")
    return count


def save_claims_to_db(client, claims: list[dict]) -> int:
    """Write Claim dicts to the anthem DB. Handles upserts by delete-and-reinsert."""
    from myhealth_fhir.db import get_anthem_session
    from myhealth_fhir.db.parser import (
        load_claim,
        load_claim_care_team,
        load_claim_diagnoses,
        load_claim_identifiers,
        load_claim_item,
    )
    from myhealth_fhir.models.anthem import (
        ClaimCareTeam,
        ClaimDiagnosis,
        ClaimIdentifier,
        ClaimItem,
        ClaimSubmission,
    )

    count = 0
    patient_ids = {
        rec.get("patient", {}).get("reference", "").split("/", 1)[-1]
        for rec in claims if isinstance(rec.get("patient"), dict) and rec["patient"].get("reference")
    }
    for patient_id in patient_ids:
        _cache_patient_identity(client, patient_id)
    with get_anthem_session() as session:
        for rec in claims:
            claim_id = rec["id"]
            existing = session.get(ClaimSubmission, claim_id)
            if existing:
                session.delete(existing)
                session.flush()
            claim = ClaimSubmission(**load_claim(rec))
            session.add(claim)
            session.flush()

            for item_rec in rec.get("item", []):
                item = ClaimItem(**load_claim_item(item_rec, claim_id))
                session.add(item)

            for dx in load_claim_diagnoses(rec, claim_id):
                session.add(ClaimDiagnosis(**dx))

            for ct in load_claim_care_team(rec, claim_id):
                session.add(ClaimCareTeam(**ct))

            for ident in load_claim_identifiers(rec, claim_id):
                session.add(ClaimIdentifier(**ident))

            count += 1
            if count % 20 == 0:
                session.flush()

        session.commit()
    try:
        from myhealth_fhir.services.member_submissions import match_registered_submissions
        matched = match_registered_submissions(eobs=[], claims=claims)
        if matched:
            log.info("Matched %d registered member submissions to claims", matched)
    except Exception:
        log.exception("Member-submission matching failed after claim save")
    return count

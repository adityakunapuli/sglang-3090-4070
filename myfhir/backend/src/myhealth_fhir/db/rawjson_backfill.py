"""Idempotent raw_json backfill.

Re-parses the ``raw_json`` stored on each row and re-populates the unpacked
columns and child tables added by the raw-json unpack migration. Runs on the
host and is safe to run concurrently with the ETL:

- header updates only set columns derived from ``raw_json`` (the same values
  the ETL would write), so they cannot clobber columns managed elsewhere;
- child rows are deleted and re-inserted per parent id, which is a stable
  FK target across the ETL's delete-and-reinsert upserts;
- each row is processed in its own short transaction to keep lock hold times
  minimal.
"""


import json
import logging

log = logging.getLogger(__name__)

_HEADER_SKIP = ("id", "raw_json")


def _reparse(raw_json):
    """Parse a stored raw_json string into a dict, or None when unusable."""
    if not raw_json:
        return None
    try:
        rec = json.loads(raw_json)
    except (ValueError, TypeError):
        return None
    return rec if isinstance(rec, dict) else None


def _header_fields(parsed: dict) -> dict:
    return {k: v for k, v in parsed.items() if k not in _HEADER_SKIP}


def backfill_anthem(dry_run: bool = False) -> dict:
    """Re-parse stored raw_json on EOB and Claim rows in myhealth_anthem."""
    from myhealth_fhir.db import get_anthem_session
    from myhealth_fhir.models.anthem import (
        EOB,
        EOBAdjudication,
        EOBIdentifier,
        EOBSupportingInfo,
        EOBProcedure,
        ClaimIdentifier,
        ClaimSubmission,
    )
    from myhealth_fhir.db.parser import (
        load_claim,
        load_claim_identifiers,
        load_eob,
        load_eob_adjudications,
        load_eob_identifiers,
        load_eob_procedures,
        load_eob_supporting_info,
    )

    stats = {"eob_total": 0, "eob_updated": 0, "eob_skipped": 0,
             "claim_total": 0, "claim_updated": 0, "claim_skipped": 0}

    # ── EOB ──────────────────────────────────────────────────────
    with get_anthem_session() as session:
        eob_rows = session.query(EOB.id, EOB.raw_json).all()

    for i, (eob_id, raw_json) in enumerate(eob_rows):
        stats["eob_total"] += 1
        rec = _reparse(raw_json)
        if rec is None:
            stats["eob_skipped"] += 1
            continue
        fields = _header_fields(load_eob(rec))
        if not dry_run:
            with get_anthem_session() as session:
                eob = session.get(EOB, eob_id)
                if eob is None:
                    stats["eob_skipped"] += 1
                    continue
                for k, v in fields.items():
                    setattr(eob, k, v)
                for model in (EOBIdentifier, EOBAdjudication, EOBSupportingInfo, EOBProcedure):
                    session.query(model).filter(model.eob_id == eob_id).delete()
                for row in load_eob_identifiers(rec, eob_id):
                    session.add(EOBIdentifier(**row))
                for row in load_eob_adjudications(rec, eob_id):
                    session.add(EOBAdjudication(**row))
                for row in load_eob_supporting_info(rec, eob_id):
                    session.add(EOBSupportingInfo(**row))
                for row in load_eob_procedures(rec, eob_id):
                    session.add(EOBProcedure(**row))
                session.commit()
        stats["eob_updated"] += 1
        if (i + 1) % 100 == 0:
            log.info("backfill eob %d/%d", i + 1, len(eob_rows))

    # ── Claim ────────────────────────────────────────────────────
    with get_anthem_session() as session:
        claim_rows = session.query(ClaimSubmission.id, ClaimSubmission.raw_json).all()

    for i, (claim_id, raw_json) in enumerate(claim_rows):
        stats["claim_total"] += 1
        rec = _reparse(raw_json)
        if rec is None:
            stats["claim_skipped"] += 1
            continue
        fields = _header_fields(load_claim(rec))
        if not dry_run:
            with get_anthem_session() as session:
                claim = session.get(ClaimSubmission, claim_id)
                if claim is None:
                    stats["claim_skipped"] += 1
                    continue
                for k, v in fields.items():
                    setattr(claim, k, v)
                session.query(ClaimIdentifier).filter(ClaimIdentifier.claim_id == claim_id).delete()
                for row in load_claim_identifiers(rec, claim_id):
                    session.add(ClaimIdentifier(**row))
                session.commit()
        stats["claim_updated"] += 1
        if (i + 1) % 100 == 0:
            log.info("backfill claim %d/%d", i + 1, len(claim_rows))

    return stats


def backfill_ucla(dry_run: bool = False) -> dict:
    """Placeholder for the UCLA clinical-table backfill (added next)."""
    raise NotImplementedError("UCLA backfill not yet implemented")

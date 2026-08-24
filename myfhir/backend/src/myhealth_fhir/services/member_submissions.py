"""Member-submitted claim tracking: manual registry + auto-matching.

Portal submission IDs (e.g. ``82a1c9d44e7b0f15``) do not exist in Anthem
FHIR. The user registers them manually via the ``member_claim_submission``
table; each EOB/Claim fetch runs ``match_registered_submissions`` to link
them to adjudicated records using strict matching.
"""
import logging
from datetime import date

from myhealth_fhir.db import get_anthem_session
from myhealth_fhir.db.models_anthem import EntityName, MemberClaimSubmission
from myhealth_fhir.db.parser import claim_number_of
from myhealth_fhir.db.identity import ref_from_fhir

log = logging.getLogger(__name__)

AMOUNT_TOLERANCE = 0.50
DATE_TOLERANCE_DAYS = 1


def _entity_provider(entity_names, reference):
    key = ref_from_fhir(reference, "anthem", "Organization")
    return entity_names.get(key)


def _service_dates(eob):
    dates = []
    bp = eob.get("billablePeriod", {})
    if bp.get("start"):
        dates.append(bp["start"][:10])
    for item in eob.get("item", []):
        if item.get("servicedDate"):
            dates.append(item["servicedDate"][:10])
        sp = item.get("servicedPeriod", {})
        if sp.get("start"):
            dates.append(sp["start"][:10])
    return dates


def _submitted_total(eob):
    total = eob.get("total")
    if isinstance(total, dict):
        # Handles both {"value": X} and {"category":..., "amount": {"value": X}} shapes.
        val = total.get("value")
        if val is not None:
            return val
        return total.get("amount", {}).get("value")
    if isinstance(total, list):
        for t in total:
            cat = t.get("category", {}).get("coding")
            if cat and cat[0].get("code") == "submitted":
                return t.get("amount", {}).get("value")
    return None


def _matches_resource(sub, resource, entity_names):
    # (a) exact claim-number match
    rcn = claim_number_of(resource)
    if sub.get("claim_number") and rcn and rcn == sub["claim_number"]:
        return True

    # (b) provider + service date + total amount
    ent = _entity_provider(entity_names, resource.get("provider", {}).get("reference"))
    if not ent:
        return False
    if sub.get("provider_npi"):
        if not ent.get("npi") or ent["npi"] != sub["provider_npi"]:
            return False
    else:
        pname = (sub.get("provider_name") or "").strip().lower()
        ename = (ent.get("name") or "").strip().lower()
        if not pname or not ename or ename != pname:
            return False
    if sub.get("service_date"):
        sd = sub["service_date"]
        sd = sd if isinstance(sd, date) else date.fromisoformat(str(sd)[:10])
        dates = [d for d in _service_dates(resource) if d]
        if not any(abs((date.fromisoformat(d) - sd).days) <= DATE_TOLERANCE_DAYS for d in dates):
            return False
    if sub.get("total_amount") is not None:
        rtotal = _submitted_total(resource)
        if rtotal is None or abs(rtotal - sub["total_amount"]) > AMOUNT_TOLERANCE:
            return False
    return True


def find_matches(submissions, eobs, claims, entity_names):
    """Return ``(submission_id, eob_id, claim_id)`` matches for unresolved rows.

    ``submissions`` is a list of dicts with keys ``id``, ``claim_number``,
    ``provider_name``, ``provider_npi``, ``service_date``, ``total_amount``,
    ``status``. ``eobs``/``claims`` are FHIR resource dicts. ``entity_names``
    maps entity id -> ``{"name": ..., "npi": ...}``.
    """
    matches: list[tuple[int, str | None, str | None]] = []
    for sub in submissions:
        if sub.get("status") == "adjudicated":
            continue
        for eob in eobs:
            if _matches_resource(sub, eob, entity_names):
                matches.append((sub["id"], eob.get("id"), None))
                break
        else:
            for claim in claims:
                if _matches_resource(sub, claim, entity_names):
                    matches.append((sub["id"], None, claim.get("id")))
                    break
    return matches


def match_registered_submissions(eobs: list[dict], claims: list[dict]) -> int:
    """Match unresolved registry rows against freshly-fetched FHIR resources.

    Opens its own anthem session, loads registry rows with status !=
    'adjudicated', resolves entity names, persists matches, and returns the
    number matched. Best-effort: raises on failure so the caller can decide
    whether to swallow it; the save-path callers log and continue.
    """
    with get_anthem_session() as session:
        rows = session.query(MemberClaimSubmission).filter(
            MemberClaimSubmission.status != "adjudicated"
        ).all()
        if not rows:
            return 0
        entity_names = {
            en.entity_ref: {"name": en.name, "npi": en.npi}
            for en in session.query(EntityName).all()
        }
        provider_names = {
            r.id: (session.get(EntityName, r.provider_ref).name
                   if r.provider_ref and session.get(EntityName, r.provider_ref) else None)
            for r in rows
        }
        submissions = [
            {"id": r.id, "claim_number": r.claim_number, "provider_name": provider_names[r.id],
             "provider_npi": r.provider_npi, "service_date": r.service_date,
             "total_amount": r.total_amount, "status": r.status}
            for r in rows
        ]
        matches = find_matches(submissions, eobs, claims, entity_names)
        by_id = {r.id: r for r in rows}
        eob_by_id = {e.get("id"): e for e in eobs}
        for sub_id, eob_id, claim_id in matches:
            row = by_id[sub_id]
            row.matched_eob_id = eob_id
            row.matched_claim_id = claim_id
            if eob_id and eob_by_id.get(eob_id, {}).get("status") != "cancelled":
                row.status = "adjudicated"
            else:
                row.status = "matched"
        session.commit()
        return len(matches)

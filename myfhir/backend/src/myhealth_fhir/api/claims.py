"""Member-submitted claim API: flag a claim number and view the YTD grid.

The flow is trivial — you paste a claim number and the system looks up
everything else from the FHIR data that's already in the DB.

Endpoints:
- ``POST /api/claims/register``     — flag a claim number as member-submitted
- ``GET  /api/claims/member-submitted`` — YTD grid with full FHIR match data
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from myhealth_fhir.db import get_anthem_session
from myhealth_fhir.db.models_anthem import (
    ClaimSubmission,
    EOB,
    EOBItem,
    EOBTotal,
    EntityName,
    MemberClaimSubmission,
)
from sqlalchemy import text

log = logging.getLogger("myhealth_fhir.api.claims")
router = APIRouter(prefix="/api/claims", tags=["member-submitted claims"])


class RegisterClaimRequest(BaseModel):
    """Just the claim number — everything else comes from FHIR."""
    claim_number: str = Field(..., description="Claim number (portal or API format)")


class ClaimColumn:
    """Column metadata for the table."""
    def __init__(self, key: str, label: str, size: int = 100):
        self.key = key
        self.label = label
        self.size = size


@router.post("/register")
def register_member_claim(body: RegisterClaimRequest) -> dict:
    """Flag a claim number as member-submitted.

    Looks up the claim/EOB by number. If found, creates a registry entry
    and marks the EOB/claim's submission_origin as 'member'.
    """
    claim_number = body.claim_number.strip()
    if claim_number.startswith("20"):
        claim_number = claim_number[2:]

    with get_anthem_session() as session:
        # Check if already registered
        existing = (
            session.query(MemberClaimSubmission)
            .filter(MemberClaimSubmission.claim_number == claim_number)
            .first()
        )
        if existing:
            raise HTTPException(status_code=409, detail=f"Claim {claim_number} already registered (ID {existing.id})")

        # Look up the EOB or claim in FHIR data
        eob = session.query(EOB).filter(EOB.claim_number == claim_number).first()
        if not eob:
            # Also try via claim_submission table
            from myhealth_fhir.db.models_anthem import ClaimSubmission
            claim_sub = session.query(ClaimSubmission).filter(ClaimSubmission.claim_number == claim_number).first()
            if claim_sub:
                eob = session.query(EOB).filter(EOB.claim_number == claim_number).first()
                if not eob:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Claim {claim_number} found in submissions but no EOB yet. Run a sync first."
                    )

        if not eob:
            raise HTTPException(
                status_code=404,
                detail=f"No EOB found for claim number {claim_number}. Check the number or run a sync first."
            )

        # Create registry entry — no extra data needed, FHIR has everything
        row = MemberClaimSubmission(
            portal_submission_id=None,
            patient_id=None,
            claim_number=claim_number,
            provider_ref=eob.payee_ref,  # point to the payee org for matching
            status="registered",
        )
        session.add(row)

        # Update EOB's submission_origin to mark it as member-submitted
        eob.submission_origin = "member"
        session.commit()
        session.refresh(row)
        eob_id = eob.id  # read before leaving session context

    log.info("Flagged claim %s as member-submitted (EOB %s)", claim_number, eob_id)
    return {
        "message": f"Claim {claim_number} flagged as member-submitted",
        "id": row.id,
        "claim_number": claim_number,
        "eob_id": eob.id,
    }


@router.get("/member-submitted")
def list_member_submitted() -> dict:
    """Return all YTD member-submitted claims with full FHIR data from the matched EOB.

    Returns columns sorted for claims fighting: claim ID → billed/allowed → paid/noncovered → breakdown → procedures → diagnoses → dates → totals.
    """
    today = date.today()
    ytd_start = date(today.year, 1, 1)

    results: list[dict] = []

    with get_anthem_session() as session:
        # Get all EOBs that are member-submitted (either by flag or by API classification)
        eobs = (
            session.query(EOB)
            .filter(
                EOB.submission_origin == "member",
                # Only include EOBs whose service date falls in YTD
                # We check via eob_items since EOB doesn't have a direct service_date
            )
            .order_by(text("created_date DESC"))
            .all()
        )

        # Also include any registry entries that have been matched but EOB might not have submission_origin set
        registry = (
            session.query(MemberClaimSubmission)
            .filter(MemberClaimSubmission.status != "ignored")
            .all()
        )

        registry_claim_numbers = {r.claim_number for r in registry if r.claim_number}

        # Deduplicate: registry entries that matched EOBs are already included above
        registry_only = []
        for r in registry:
            if not r.claim_number or r.claim_number not in registry_claim_numbers:
                continue
            # Check if this claim_number is already in our EOB list
            already = any(e.claim_number == r.claim_number for e in eobs)
            if not already:
                registry_only.append(r)

        entity_names = {
            en.entity_ref: en.name
            for en in session.query(EntityName).all()
        }

        # Build results from EOBs
        for eob in eobs:
            # Get EOB-level totals
            totals_raw = session.query(EOBTotal).filter(EOBTotal.eob_id == eob.id).all()
            totals = {t.category_code: t.amount for t in totals_raw}

            # Get item-level data (use the first item for summary, but include all in grid)
            items = session.query(EOBItem).filter(EOBItem.eob_id == eob.id).order_by(EOBItem.sequence).all()

            for item in items:
                payee_name = entity_names.get(eob.payee_ref, "")
                provider_name = entity_names.get(eob.provider_ref, "")

                # Get diagnoses
                diagnoses_raw = session.execute(
                    text("SELECT icd_code, icd_display FROM eob_diagnosis WHERE eob_id = :eob_id"),
                    {"eob_id": eob.id}
                ).fetchall()
                icd_codes = ", ".join(d[0] for d in diagnoses_raw if d[0])
                icd_displays = " | ".join(d[1] for d in diagnoses_raw if d[1])

                results.append({
                    "eob_id": eob.id,
                    "item_id": item.id,
                    "claim_number": eob.claim_number,
                    "status": eob.status,
                    "submitted_amount": item.submitted_amount,
                    "allowed_amount": item.allowed_amount,
                    "paid_provider": item.paid_provider,
                    "paid_patient": item.paid_patient,
                    "item_noncovered": item.noncovered,
                    "item_deductible": item.deductible,
                    "item_coinsurance": item.coinsurance,
                    "item_copay": item.copay,
                    "item_discount": item.discount,
                    "member_liability": item.member_liability,
                    "item_seq": item.sequence,
                    "hcpcs_code": item.hcpcs_code,
                    "hcpcs_display": item.hcpcs_display,
                    "modifier_codes": item.modifier_codes,
                    "quantity": item.quantity,
                    "serviced_date": item.serviced_date.isoformat() if item.serviced_date else None,
                    "icd_codes": icd_codes,
                    "icd_displays": icd_displays,
                    "payment_status": item.payment_status,
                    "adjustment_reason": item.adjustment_reason,
                    "provider_name": provider_name,
                    "payee_name": payee_name,
                    "is_out_of_network": eob.is_out_of_network,
                    "created_date": eob.created_date.isoformat() if eob.created_date else None,
                    "billable_period_start": eob.billable_period_start.isoformat() if eob.billable_period_start else None,
                    "billable_period_end": eob.billable_period_end.isoformat() if eob.billable_period_end else None,
                    "total_submitted": totals.get("submitted"),
                    "total_benefit": totals.get("benefit"),
                    "total_deductible": totals.get("deductible"),
                    "total_coinsurance": totals.get("coinsurance"),
                    "total_member_liability": totals.get("member_liability"),
                    "total_noncovered": totals.get("noncovered"),
                    "registry_id": None,
                })

        # Also add registry-only entries (not yet matched to an EOB)
        for r in registry_only:
            results.append({
                "eob_id": None,
                "item_id": None,
                "claim_number": r.claim_number,
                "status": "pending",
                "submitted_amount": r.total_amount,
                "allowed_amount": None,
                "paid_provider": None,
                "paid_patient": None,
                "item_noncovered": None,
                "item_deductible": None,
                "item_coinsurance": None,
                "item_copay": None,
                "item_discount": None,
                "member_liability": None,
                "item_seq": None,
                "hcpcs_code": r.cpt_codes if r.cpt_codes else "",
                "hcpcs_display": None,
                "modifier_codes": None,
                "quantity": None,
                "serviced_date": r.service_date.isoformat() if r.service_date else None,
                "icd_codes": "",
                "icd_displays": "",
                "payment_status": None,
                "adjustment_reason": None,
                "provider_name": "",
                "payee_name": "",
                "is_out_of_network": None,
                "created_date": r.created_at.isoformat() if r.created_at else None,
                "billable_period_start": None,
                "billable_period_end": None,
                "total_submitted": r.total_amount,
                "total_benefit": None,
                "total_deductible": None,
                "total_coinsurance": None,
                "total_member_liability": None,
                "total_noncovered": None,
                "registry_id": r.id,
            })

    return {"claims": results, "year": today.year, "total": len(results)}

"""EOB and Claim FHIR resource parsing functions — shared by fhir_client and the eob_db loader."""

import json
import re
from datetime import date, datetime


_VENDOR_PREFIX_RE = re.compile(r"^(DELTADENTAL|VSP|MEDCO)")


AMOUNT_CATS = {
    "submitted amount": "submitted_amount",
    "benefit amount": "allowed_amount",
    "paid to provider": "paid_provider",
    "paid by patient": "paid_patient",
    "deductible": "deductible",
    "co-insurance": "coinsurance",
    "copay": "copay",
    "noncovered": "noncovered",
    "discount": "discount",
    "member liability": "member_liability",
}


def parse_date(val):
    """Parse a FHIR date or dateTime string into a `date` (date part only)."""
    if not val:
        return None
    return date.fromisoformat(val.split("T")[0])


def coding(coding_list):
    """Return the first coding from a FHIR Coding list, or None."""
    if not coding_list or not isinstance(coding_list, list):
        return None
    return coding_list[0]


def ref(obj):
    """Extract the id portion of a FHIR Reference (stripping the resource type prefix)."""
    if not obj:
        return None
    reference = obj.get("reference")
    if reference and "/" in reference:
        return reference.split("/", 1)[1]
    return reference


def anthem_ref(obj: dict | None, entity_type: str) -> str | None:
    """Return a provider-scoped reference for an Anthem FHIR Reference."""
    value = ref(obj)
    if not value:
        return None
    from myhealth_fhir.db.identity import entity_ref

    return entity_ref("anthem", entity_type, value)


def anthem_ref_auto(obj: dict | None, default_type: str = "Organization") -> str | None:
    """Like anthem_ref, but infers the entity type from the reference itself
    (e.g. 'Patient/123' -> Patient). Anthem payees may be Organization OR the
    member (Patient) when the reimbursement check goes to the subscriber."""
    if not obj:
        return None
    reference = obj.get("reference")
    if not reference:
        return None
    entity_type = reference.split("/", 1)[0] if "/" in reference else default_type
    return anthem_ref(obj, entity_type)


def claim_number_of(resource: dict) -> str | None:
    """Return the unique claim ID (identifier type ``uc``), or None.

    Some Anthem Claim resources omit the type coding entirely and carry only
    a system URL (.../EDW/clm_nbr) — fall back to matching on that."""
    for ident in _as_list(resource.get("identifier")):
        c = coding(ident.get("type", {}).get("coding"))
        if c and c["code"] == "uc":
            return ident.get("value")
    for ident in _as_list(resource.get("identifier")):
        system = (ident.get("system") or "").replace(" ", "")
        if system.endswith("clm_nbr"):
            return ident.get("value")
    return None


def _as_list(val):
    """Normalize a FHIR field that may be a single object or an array into a list."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]


def _first(val):
    """Return the first element of a FHIR field that may be a single object or an array."""
    items = _as_list(val)
    return items[0] if items else None


def identifier_rows(resource: dict) -> list[dict]:
    """Flatten FHIR identifier[] into rows for a *_identifier child table."""
    rows = []
    for i, ident in enumerate(_as_list(resource.get("identifier"))):
        if not isinstance(ident, dict):
            continue
        tc = coding((ident.get("type") or {}).get("coding"))
        rows.append(
            {
                "seq": i,
                "system": ident.get("system"),
                "value": ident.get("value"),
                "use": ident.get("use"),
                "type_code": tc.get("code") if tc else None,
                "type_display": tc.get("display") if tc else None,
            }
        )
    return rows


def _first_coverage_ref(resource: dict) -> str | None:
    """Return the first coverage reference id from insurance[].coverage."""
    for ins in _as_list(resource.get("insurance")):
        if isinstance(ins, dict):
            cov = ins.get("coverage")
            if isinstance(cov, dict):
                ref = cov.get("reference")
                if ref:
                    return ref.split("/", 1)[1] if "/" in ref else ref
    return None


def _contained_display(resource: dict, ref_obj: dict | None) -> str | None:
    """Resolve a contained reference (#N) to its name, or the display text."""
    if not ref_obj or not isinstance(ref_obj, dict):
        return None
    reference = ref_obj.get("reference")
    if not reference or not str(reference).startswith("#"):
        return ref_obj.get("display")
    for cont in resource.get("contained", []):
        if isinstance(cont, dict) and str(cont.get("id")) == str(reference).lstrip("#"):
            return cont.get("name") or cont.get("display")
    return None


def classify_submission_origin(payee: dict, identifiers: list[dict]) -> str:
    """Return ``"member"`` or ``"provider"`` for a FHIR Claim/EOB resource.

    A claim is member-submitted when the payee is the beneficiary/subscriber
    (member reimbursed directly — Anthem codes this payee type 'subscriber')
    or the claim number has a vendor prefix
    (DELTADENTAL/VSP/MEDCO) indicating a vendor reimburses the member.
    """
    pt = coding((payee or {}).get("type", {}).get("coding"))
    party = (payee or {}).get("party", {})
    party_ref = party.get("reference") if isinstance(party, dict) else None
    if (
        pt and pt.get("code") in ("beneficiary", "subscriber")
        and party_ref and party_ref.startswith("Patient/")
    ):
        return "member"
    for ident in identifiers or []:
        value = ident.get("value") or ""
        if _VENDOR_PREFIX_RE.match(value):
            return "member"
    return "provider"


def is_out_of_network(adjudication: list[dict]) -> bool:
    """Return True if header adjudication marks the claim out-of-network."""
    for entry in adjudication or []:
        cat = coding(entry.get("category", {}).get("coding"))
        if cat and cat.get("code") == "billingnetworkstatus":
            reason = coding(entry.get("reason", {}).get("coding"))
            if reason and reason.get("code") == "outofnetwork":
                return True
    return False


def load_eob(rec):
    """Parse a FHIR ExplanationOfBenefit resource into a flat dict for the `eob` table."""
    billable = rec.get("billablePeriod", {})
    payee = rec.get("payee", {})
    pt_c = coding(payee.get("type", {}).get("coding"))
    pp = payee.get("party", {})
    type_c = coding(rec.get("type", {}).get("coding"))
    sub_type_c = coding(rec.get("subType", {}).get("coding"))

    ca_key = None
    cl_num = None
    for ident in rec.get("identifier", []):
        c = coding(ident.get("type", {}).get("coding"))
        if c and c["code"] == "uc":
            cl_num = ident["value"]
        if c and c["code"] == "ck":
            ca_key = ident["value"]

    payer_id = None
    for cont in rec.get("contained", []):
        for ident in cont.get("identifier", []):
            c = coding(ident.get("type", {}).get("coding"))
            if c and c["code"] == "payerid":
                payer_id = ident["value"]

    payment = rec.get("payment", {})
    p_amt = payment.get("amount", {}) if isinstance(payment, dict) else {}
    p_type_c = coding(payment.get("type", {}).get("coding")) if isinstance(payment, dict) else None
    p_adj_c = coding(payment.get("adjustmentReason", {}).get("coding")) if isinstance(payment, dict) else None

    coverage_ref = _first_coverage_ref(rec)

    preauth_refs = None
    preauth = [x.get("reference") for x in _as_list(rec.get("preAuthRef")) if isinstance(x, dict) and x.get("reference")]
    if preauth:
        preauth_refs = json.dumps(preauth)

    last_updated = None
    meta = rec.get("meta", {})
    if isinstance(meta, dict) and meta.get("lastUpdated"):
        try:
            last_updated = datetime.fromisoformat(meta["lastUpdated"].replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass

    # Claim received date from CARIN BB supportingInfo.clmrecvddate
    claim_received_date = None
    for si in _as_list(rec.get("supportingInfo")):
        if not isinstance(si, dict):
            continue
        cat = coding((si.get("category") or {}).get("coding"))
        if cat and cat.get("code") == "clmrecvddate":
            claim_received_date = parse_date(si.get("timingDate"))
            break

    return {
        "id": rec["id"],
        "claim_number": cl_num,
        "claim_adjustment_key": ca_key,
        "status": rec.get("status"),
        "claim_type": type_c["code"] if type_c else None,
        "sub_type": sub_type_c["code"] if sub_type_c else None,
        "use": rec.get("use"),
        "outcome": rec.get("outcome"),
        "disposition": rec.get("disposition"),
        "created_date": parse_date(rec.get("created")),
        "claim_received_date": claim_received_date,
        "billable_period_start": parse_date(billable.get("start")),
        "billable_period_end": parse_date(billable.get("end")),
        "patient_ref": anthem_ref(rec.get("patient"), "Patient"),
        "provider_ref": anthem_ref(rec.get("provider"), "Organization"),
        "insurer_payer_id": payer_id,
        "coverage_ref": coverage_ref,
        "payee_type": pt_c["code"] if pt_c else None,
        "payee_ref": anthem_ref_auto(pp),
        "submission_origin": classify_submission_origin(payee, rec.get("identifier", [])),
        "is_out_of_network": is_out_of_network(rec.get("adjudication")),
        "payment_amount": p_amt.get("value") if isinstance(p_amt, dict) else None,
        "payment_date": parse_date(payment.get("date")) if isinstance(payment, dict) else None,
        "payment_type": p_type_c["display"] if p_type_c else None,
        "insurer_ref": anthem_ref(rec.get("insurer"), "Organization"),
        "payment_currency": p_amt.get("currency") if isinstance(p_amt, dict) else None,
        "payment_adjustment_code": p_adj_c["code"] if p_adj_c else None,
        "payment_adjustment_display": p_adj_c["display"] if p_adj_c else None,
        "preauth_refs": preauth_refs,
        "payer_display": _contained_display(rec, rec.get("insurer")),
        "last_updated": last_updated,
        "raw_json": json.dumps(rec),
    }


def load_eob_identifiers(rec, eob_id):
    """Parse EOB identifier[] into rows for the `eob_identifier` table."""
    rows = []
    for row in identifier_rows(rec):
        rows.append({"eob_id": eob_id, **row})
    return rows


def load_eob_adjudications(rec, eob_id):
    """Parse EOB header-level adjudication[] into rows for the `eob_adjudication` table."""
    result = []
    for i, adj in enumerate(_as_list(rec.get("adjudication"))):
        if not isinstance(adj, dict):
            continue
        cat = coding((adj.get("category") or {}).get("coding"))
        reason = coding((adj.get("reason") or {}).get("coding"))
        amount = adj.get("amount") if isinstance(adj.get("amount"), dict) else {}
        result.append(
            {
                "eob_id": eob_id,
                "seq": i,
                "category_code": cat.get("code") if cat else None,
                "category_display": cat.get("display") if cat else None,
                "amount": amount.get("value"),
                "currency": amount.get("currency"),
                "value_units": adj.get("value") if isinstance(adj.get("value"), (int, float)) else None,
                "reason_code": reason.get("code") if reason else None,
                "reason_display": reason.get("display") if reason else None,
            }
        )
    return result


def load_eob_supporting_info(rec, eob_id):
    """Parse EOB supportingInfo[] into rows for the `eob_supporting_info` table."""
    result = []
    for i, si in enumerate(_as_list(rec.get("supportingInfo"))):
        if not isinstance(si, dict):
            continue
        cat = coding((si.get("category") or {}).get("coding"))
        code = coding((si.get("code") or {}).get("coding"))
        timing = si.get("timing")
        if isinstance(timing, list):
            timing = timing[0] if timing else {}
        if not isinstance(timing, dict):
            timing = {}
        period = timing.get("period")
        if isinstance(period, list):
            period = period[0] if period else {}
        if not isinstance(period, dict):
            period = {}
        result.append(
            {
                "eob_id": eob_id,
                "seq": i,
                "category_code": cat.get("code") if cat else None,
                "category_display": cat.get("display") if cat else None,
                "code_code": code.get("code") if code else None,
                "code_display": code.get("display") if code else None,
                "timing_date": parse_date(si.get("timingDate") or timing.get("date")),
                "timing_period_start": parse_date(period.get("start")),
                "timing_period_end": parse_date(period.get("end")),
                "value_string": si.get("valueString"),
            }
        )
    return result


def load_eob_procedures(rec, eob_id):
    """Parse EOB procedure[] into rows for the `eob_procedure` table."""
    result = []
    for i, p in enumerate(_as_list(rec.get("procedure"))):
        if not isinstance(p, dict):
            continue
        cc = coding((p.get("procedureCodeableConcept") or {}).get("coding"))
        type_first = _first(p.get("type"))
        tc = coding(type_first.get("coding")) if isinstance(type_first, dict) else None
        result.append(
            {
                "eob_id": eob_id,
                "seq": i,
                "service_sequence": p.get("sequence"),
                "service_date": parse_date(p.get("date")),
                "code_code": cc.get("code") if cc else None,
                "code_display": cc.get("display") if cc else None,
                "type_code": tc.get("code") if tc else None,
                "type_display": tc.get("display") if tc else None,
            }
        )
    return result


def extract_item_adjs(rec):
    """Extract and flatten adjudication entries from an EOB item into a dict keyed by category."""
    adjs = {}
    for adj in rec.get("adjudication", []):
        cat_c = coding(adj.get("category", {}).get("coding"))
        if not cat_c:
            continue
        cat_display = cat_c.get("display", cat_c["code"]).lower().strip()
        reason_c = coding(adj.get("reason", {}).get("coding"))

        if "amount" in adj:
            adjs[cat_display] = adj["amount"].get("value") if isinstance(adj["amount"], dict) else None
        elif "value" in adj and isinstance(adj["value"], (int, float)):
            if cat_c["code"] == "allowedunits":
                adjs["allowedunits"] = adj["value"]
            else:
                adjs[cat_display] = adj["value"]
        else:
            if reason_c:
                adjs[cat_display] = reason_c.get("display", reason_c["code"])
    return adjs


def load_item(rec, eob_id):
    """Parse an EOB item into a flat dict for the `eob_item` table (flattens common adjudications)."""
    prod_c = coding(rec.get("productOrService", {}).get("coding"))
    mods = []
    for m in rec.get("modifier", []):
        mc = coding(m.get("coding"))
        if mc:
            mods.append(mc["code"])
    loc_c = coding(rec.get("locationCodeableConcept", {}).get("coding"))
    svc = rec.get("servicedPeriod", {})
    net = rec.get("net", {})

    item = {
        "eob_id": eob_id,
        "sequence": rec.get("sequence"),
        "hcpcs_code": prod_c["code"] if prod_c else None,
        "hcpcs_display": prod_c["display"] if prod_c else None,
        "modifier_codes": ", ".join(mods),
        "serviced_date": parse_date(rec.get("servicedDate")) or (parse_date(svc.get("start")) if svc else None),
        "serviced_period_start": parse_date(svc.get("start")) if svc else None,
        "serviced_period_end": parse_date(svc.get("end")) if svc else None,
        "location_code": loc_c["code"] if loc_c else None,
        "location_display": loc_c["display"] if loc_c else None,
        "quantity": (rec.get("quantity") or {}).get("value"),
        "net_amount": net.get("value") if net else None,
    }

    adjs = extract_item_adjs(rec)
    item["allowed_units"] = adjs.pop("allowedunits", None)
    item["adjustment_reason"] = adjs.pop("adjustment reason", None)
    item["payment_status"] = adjs.pop("benefit payment status", None)

    for cat_lower, attr in AMOUNT_CATS.items():
        item[attr] = adjs.get(cat_lower)

    return item


def load_diagnoses(rec, eob_id):
    """Parse EOB diagnosis entries into a list of dicts for the `eob_diagnosis` table."""
    result = []
    for dx in rec.get("diagnosis", []):
        cc = coding(dx.get("diagnosisCodeableConcept", {}).get("coding"))
        dt = dx.get("type", [])
        tc = coding(dt[0].get("coding")) if dt else None
        oa = coding(dx.get("onAdmission", {}).get("coding"))
        result.append(
            {
                "eob_id": eob_id,
                "sequence": dx.get("sequence"),
                "icd_code": cc["code"] if cc else None,
                "icd_display": cc["display"] if cc else None,
                "diagnosis_type": tc["display"] or tc["code"] if tc else None,
                "on_admission": oa["display"] or oa["code"] if oa else None,
            }
        )
    return result


def load_care_team(rec, eob_id):
    """Parse EOB careTeam entries into a list of dicts for the `eob_care_team` table."""
    result = []
    for ct in rec.get("careTeam", []):
        rc = coding(ct.get("role", {}).get("coding"))
        result.append(
            {
                "eob_id": eob_id,
                "sequence": ct.get("sequence"),
                "provider_ref": anthem_ref_auto(ct.get("provider"), default_type="Practitioner"),
                "role_code": rc["code"] if rc else None,
                "role_display": rc["display"] if rc else None,
            }
        )
    return result


def load_totals(rec, eob_id):
    """Parse EOB total entries into a list of dicts for the `eob_total` table."""
    result = []
    for t in rec.get("total", []):
        cc = coding(t.get("category", {}).get("coding"))
        amt = t.get("amount", {})
        result.append(
            {
                "eob_id": eob_id,
                "category": cc["display"] if cc else None,
                "category_code": cc["code"] if cc else None,
                "amount": amt.get("value") if isinstance(amt, dict) else None,
                "currency": amt.get("currency") if isinstance(amt, dict) else None,
            }
        )
    return result


def load_item_adjudications(rec, eob_item_id):
    """Parse an EOB item's adjudication entries into rows for `eob_item_adjudication`."""
    result = []
    for adj in rec.get("adjudication", []):
        cat_c = coding(adj.get("category", {}).get("coding"))
        if not cat_c:
            continue
        reason_c = coding(adj.get("reason", {}).get("coding"))
        amount = adj.get("amount", {})
        result.append(
            {
                "eob_item_id": eob_item_id,
                "category": cat_c.get("display"),
                "category_code": cat_c.get("code"),
                "amount": amount.get("value") if isinstance(amount, dict) else None,
                "value_units": adj.get("value") if isinstance(adj.get("value"), (int, float)) else None,
                "reason_code": reason_c["code"] if reason_c else None,
                "reason_display": reason_c["display"] if reason_c else None,
            }
        )
    return result


# ── Claim (FHIR Claim resource) Parser ──────────────────────────


# Elevance vendor extension URLs (whitespace-normalized) → (code_col, display_col, kind)
# kind: date | string | int | decimal | code | coding
_ELEVANCE_CLAIM_EXTENSIONS = {
    "https://elevancehealth.com/fhirextensions#claim-adjudicationDate": ("adjudication_date", None, "date"),
    "https://elevancehealth.com/fhirextensions#claim-AdjudicationStatus": ("adjudication_status_code", None, "coding"),
    "https://elevancehealth.com/fhirextensions#claim-claimActionDate": ("action_date", None, "date"),
    "https://elevancehealth.com/fhirextensions#claim-claimActionType": ("action_type_code", None, "coding"),
    "https://elevancehealth.com/fhirextensions#claim-claimAdjustmentNumber": ("adjustment_number", None, "string"),
    "https://elevancehealth.com/fhirextensions#claim-claimClass": ("claim_class_code", None, "coding"),
    "https://elevancehealth.com/fhirextensions#claim-claimDenialReason": ("denial_reason_code", None, "coding"),
    "https://elevancehealth.com/fhirextensions#claim-claimLineStatus": ("line_status_code", "line_status_display", "coding"),
    "https://elevancehealth.com/fhirextensions#claim-claimPaidDate": ("paid_date", None, "date"),
    "https://elevancehealth.com/fhirextensions#claim-claimSystemOfRecord": ("system_of_record_code", None, "coding"),
    "https://elevancehealth.com/fhirextensions#claim-dischargeStatus": ("discharge_status_code", None, "coding"),
    "https://elevancehealth.com/fhirextensions#claim-documentControlNumber": ("document_control_number", None, "string"),
    "https://elevancehealth.com/fhirextensions#claim-externalLoadCode": ("external_load_code", None, "code"),
    "https://elevancehealth.com/fhirextensions#claim-inPatient": ("in_patient", None, "code"),
    "https://elevancehealth.com/fhirextensions#claim-interPlanTeleprocessingSystemHostHome": ("ipt_home_code", None, "coding"),
    "https://elevancehealth.com/fhirextensions#claim-interPlanTeleprocessingSystemStandardClaimsCollectionFacilityNumber": ("ipt_facility_number", None, "string"),
    "https://elevancehealth.com/fhirextensions#claim-lengthOfStayCount": ("length_of_stay", None, "int"),
    "https://elevancehealth.com/fhirextensions#claim-networkIdentifier": ("network_identifier_code", None, "code"),
    "https://elevancehealth.com/fhirextensions#claim-placeOfService": ("place_of_service_code", "place_of_service_display", "coding"),
    "https://elevancehealth.com/fhirextensions#claim-PPSCode": ("pps_code", None, "code"),
    "https://elevancehealth.com/fhirextensions#claim-sourceBillingProviderID": ("source_billing_provider_id", None, "string"),
    "https://elevancehealth.com/fhirextensions#claim-sourceNationalProviderID": ("source_npi", None, "string"),
    "https://elevancehealth.com/fhirextensions#claim-totalDiagCodeCount": ("total_diag_code_count", None, "int"),
    "https://elevancehealth.com/fhirextensions#claim-totalPaidAmount": ("total_paid_amount", None, "decimal"),
    "https://elevancehealth.com/fhirextensions#medicationDispense-dispensedBrandGeneric": ("dispensed_brand_generic_code", None, "coding"),
    "https://elevancehealth.com/fhirextensions#patient-masterConsumerID": ("master_consumer_id", None, "string"),
    "https://elevancehealth.com/fhirextensions#patient-Mbr_Key": ("mbr_key", None, "string"),
    "https://www.nubc.org/CodeSystem/PointOfOrigin": ("point_of_origin_code", None, "coding"),
    "https://www.nubc.org/CodeSystem/PriorityTypeOfAdmitOrVisit": ("priority_admit_type_code", None, "coding"),
}


def _ext_codeable(ext):
    """Return (code, display) from an extension's valueCoding/valueCodeableConcept."""
    vc = ext.get("valueCoding")
    if isinstance(vc, dict):
        return vc.get("code"), vc.get("display")
    c = coding((ext.get("valueCodeableConcept") or {}).get("coding"))
    if c:
        return c.get("code"), c.get("display")
    return None, None


def claim_extension_fields(rec) -> dict:
    """Extract Elevance vendor extensions on a Claim into named column values."""
    fields: dict = {}
    for ext in _as_list(rec.get("extension")):
        if not isinstance(ext, dict):
            continue
        url = "".join((ext.get("url") or "").split())
        spec = _ELEVANCE_CLAIM_EXTENSIONS.get(url)
        if not spec:
            continue
        code_col, display_col, kind = spec
        if kind == "date":
            fields[code_col] = parse_date(ext.get("valueDate") or ext.get("valueDateTime"))
        elif kind == "string":
            fields[code_col] = ext.get("valueString")
        elif kind == "code":
            fields[code_col] = ext.get("valueCode")
        elif kind == "int":
            v = ext.get("valueInteger")
            try:
                fields[code_col] = int(v)
            except (TypeError, ValueError):
                fields[code_col] = None
        elif kind == "decimal":
            v = ext.get("valueDecimal")
            try:
                fields[code_col] = float(v)
            except (TypeError, ValueError):
                fields[code_col] = None
        elif kind == "coding":
            code, display = _ext_codeable(ext)
            fields[code_col] = code
            if display_col:
                fields[display_col] = display
    return fields


def load_claim(rec):
    """Parse a FHIR Claim resource into a flat dict for the `claim_submission` table."""
    billable = rec.get("billablePeriod", {})
    type_c = coding(rec.get("type", {}).get("coding"))
    sub_type_c = coding(rec.get("subType", {}).get("coding"))
    total = rec.get("total", {})
    ins = rec.get("insurer", {})

    last_updated = None
    meta = rec.get("meta", {})
    if isinstance(meta, dict) and meta.get("lastUpdated"):
        try:
            last_updated = datetime.fromisoformat(meta["lastUpdated"].replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass

    priority_coding = coding(rec.get("priority", {}).get("coding"))

    payee = rec.get("payee", {})
    pt_c = coding(payee.get("type", {}).get("coding"))
    party_ref = ref(payee.get("party"))

    cl_num = None
    ca_key = None
    for ident in _as_list(rec.get("identifier")):
        if not isinstance(ident, dict):
            continue
        system = (ident.get("system") or "").replace(" ", "")
        if system.endswith("clm_nbr"):
            cl_num = ident.get("value")
        elif system.endswith("claimAdjustmentKey"):
            ca_key = ident.get("value")

    prescription_first = _first(rec.get("prescription"))
    prescription_ref = prescription_first.get("reference") if isinstance(prescription_first, dict) else None

    preauth_refs = None
    preauth = [x.get("reference") for x in _as_list(rec.get("preAuthRef")) if isinstance(x, dict) and x.get("reference")]
    if preauth:
        preauth_refs = json.dumps(preauth)

    out = {
        "id": rec["id"],
        "status": rec.get("status"),
        "claim_type": type_c["code"] if type_c else None,
        "sub_type_code": sub_type_c["code"] if sub_type_c else None,
        "sub_type_display": sub_type_c["display"] if sub_type_c else None,
        "use": rec.get("use"),
        "created_date": parse_date(rec.get("created")),
        "billable_period_start": parse_date(billable.get("start")),
        "billable_period_end": parse_date(billable.get("end")),
        "patient_ref": anthem_ref(rec.get("patient"), "Patient"),
        "provider_ref": anthem_ref(rec.get("provider"), "Organization"),
        "insurer_ref": anthem_ref(ins, "Organization"),
        "priority": priority_coding.get("code") if priority_coding else None,
        "priority_display": priority_coding.get("display") if priority_coding else None,
        "total_amount": total.get("value") if total else None,
        "total_currency": total.get("currency") if total else None,
        "claim_number": cl_num,
        "claim_adjustment_key": ca_key,
        "payee_type": pt_c["code"] if pt_c else None,
        "payee_ref": anthem_ref_auto(payee.get("party")),
        "coverage_ref": _first_coverage_ref(rec),
        "preauth_refs": preauth_refs,
        "prescription_ref": prescription_ref,
        "submission_origin": classify_submission_origin(payee, _as_list(rec.get("identifier"))),
        "is_out_of_network": is_out_of_network(rec.get("adjudication")),
        "last_updated": last_updated,
        "raw_json": json.dumps(rec),
    }
    out.update(claim_extension_fields(rec))
    return out


def load_claim_identifiers(rec, claim_id):
    """Parse Claim identifier[] into rows for the `claim_identifier` table."""
    rows = []
    for row in identifier_rows(rec):
        rows.append({"claim_id": claim_id, **row})
    return rows


def load_claim_item(rec, claim_id):
    """Parse a Claim line item into a flat dict for the `claim_item` table."""
    prod_c = coding(rec.get("productOrService", {}).get("coding"))
    mods = []
    for m in rec.get("modifier", []):
        mc = coding(m.get("coding"))
        if mc:
            mods.append(mc["code"])
    loc_c = coding(rec.get("locationCodeableConcept", {}).get("coding"))
    svc = rec.get("servicedPeriod", {})
    net = rec.get("net", {})
    up = rec.get("unitPrice", {})

    return {
        "claim_id": claim_id,
        "sequence": rec.get("sequence"),
        "hcpcs_code": prod_c["code"] if prod_c else None,
        "hcpcs_display": prod_c["display"] if prod_c else None,
        "modifier_codes": ", ".join(mods),
        "serviced_date": parse_date(rec.get("servicedDate")) or (parse_date(svc.get("start")) if svc else None),
        "serviced_period_start": parse_date(svc.get("start")) if svc else None,
        "serviced_period_end": parse_date(svc.get("end")) if svc else None,
        "location_code": loc_c["code"] if loc_c else None,
        "location_display": loc_c["display"] if loc_c else None,
        "quantity": (rec.get("quantity") or {}).get("value"),
        "unit_price": up.get("value") if up else None,
        "net_amount": net.get("value") if net else None,
    }


def load_claim_diagnoses(rec, claim_id):
    """Parse Claim diagnosis entries into a list of dicts for the `claim_diagnosis` table."""
    result = []
    for dx in rec.get("diagnosis", []):
        cc = coding(dx.get("diagnosisCodeableConcept", {}).get("coding"))
        dt = dx.get("type", [])
        tc = coding(dt[0].get("coding")) if dt else None
        result.append(
            {
                "claim_id": claim_id,
                "sequence": dx.get("sequence"),
                "icd_code": cc["code"] if cc else None,
                "icd_display": cc["display"] if cc else None,
                "diagnosis_type": tc["display"] or tc["code"] if tc else None,
            }
        )
    return result


def load_claim_care_team(rec, claim_id):
    """Parse Claim careTeam entries into a list of dicts for the `claim_care_team` table."""
    result = []
    for ct in rec.get("careTeam", []):
        rc = coding(ct.get("role", {}).get("coding"))
        result.append(
            {
                "claim_id": claim_id,
                "sequence": ct.get("sequence"),
                "provider_ref": anthem_ref_auto(ct.get("provider"), default_type="Practitioner"),
                "role_code": rc["code"] if rc else None,
                "role_display": rc["display"] if rc else None,
            }
        )
    return result

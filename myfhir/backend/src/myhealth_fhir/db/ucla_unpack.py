"""Pure FHIR extractors for the UCLA raw_json unpack.

Each ``extract_<resource>(res)`` takes a parsed FHIR resource dict and returns
``(header, children)``:

- ``header`` — ``{column: value}`` for the NEW unpacked columns on the parent
  table. Values are pure FHIR derivations (no DB access) so the same code path
  works in the ETL save functions and the raw_json backfill. Reference columns
  hold the raw FHIR reference string (same convention as
  ``diagnostic_report.specimen_ref``).
- ``children`` — ``{child_key: [row dicts]}``. Row keys match the child model
  columns exactly. Identifier-style rows carry their FK column pre-set to the
  resource id; the component rows (``lab_result_component`` /
  ``clinical_observation_component``) OMIT the FK because it is the parent's
  integer PK — the consumer sets ``lab_id`` / ``observation_id`` after flush.

``extract_immunization`` additionally returns the raw encounter reference under
the non-column key ``encounter_ref``; consumers resolve it to a local
``encounter_id`` (FK safety) before applying.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time


def _as_list(val):
    if val is None:
        return []
    return val if isinstance(val, list) else [val]


def _first(val):
    items = _as_list(val)
    return items[0] if items else None


def _dt(value):
    """Parse a FHIR dateTime or date string into a datetime (date → midnight)."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    s = str(value).strip()
    if not s:
        return None
    for candidate in (s.replace("Z", "+00:00"), s, s.split("T")[0]):
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            continue
    return None


def _date(value):
    dt = _dt(value)
    return dt.date() if dt else None


def _cc(cc):
    """First coding of a CodeableConcept → ``(code, display)`` or ``(None, None)``."""
    if not isinstance(cc, dict):
        return None, None
    for c in cc.get("coding") or []:
        if isinstance(c, dict) and (c.get("code") or c.get("display")):
            return c.get("code"), c.get("display")
    return None, None


def _cd(cc):
    """First coding display of a CodeableConcept, else its ``text``."""
    if not isinstance(cc, dict):
        return None
    for c in cc.get("coding") or []:
        if isinstance(c, dict) and c.get("display"):
            return c["display"]
    return cc.get("text")


def _raw_ref(obj):
    """Raw FHIR reference string of a Reference object, or None."""
    return obj.get("reference") if isinstance(obj, dict) else None


def _ref_display(obj):
    return obj.get("display") if isinstance(obj, dict) else None


def _float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ident_rows(resource, fk_key, fk_value):
    """Flatten FHIR identifier[] into child rows (seq, system, value, use + FK)."""
    rows = []
    for i, ident in enumerate(_as_list(resource.get("identifier"))):
        if not isinstance(ident, dict):
            continue
        rows.append(
            {
                fk_key: fk_value,
                "seq": i,
                "system": ident.get("system"),
                "value": ident.get("value"),
                "use": ident.get("use"),
            }
        )
    return rows


def _ext_bool(resource, url):
    """valueBoolean of the first top-level extension matching ``url`` (None if absent)."""
    for ext in _as_list(resource.get("extension")):
        if isinstance(ext, dict) and "".join((ext.get("url") or "").split()) == url:
            v = ext.get("valueBoolean")
            if v is None:
                return None
            return bool(v)
    return None


def _ref_range_text(ref_ranges):
    """Join referenceRange[] texts (or low/high fallbacks) into one string."""
    parts = []
    for r in _as_list(ref_ranges):
        if not isinstance(r, dict):
            continue
        if r.get("text"):
            parts.append(str(r["text"]))
            continue
        low = r.get("low") if isinstance(r.get("low"), dict) else {}
        high = r.get("high") if isinstance(r.get("high"), dict) else {}
        lv, hv = low.get("value"), high.get("value")
        lu = low.get("unit", "")
        if lv is not None and hv is not None:
            parts.append(f"{lv}-{hv} {lu}".strip())
        elif lv is not None:
            parts.append(f">={lv} {lu}".strip())
        elif hv is not None:
            parts.append(f"<={hv} {high.get('unit', '')}".strip())
    return ", ".join(parts) if parts else None


def _notes(resource):
    parts = [n["text"] for n in _as_list(resource.get("note")) if isinstance(n, dict) and n.get("text")]
    return "; ".join(parts) if parts else None


def _obs_value(obs):
    """Human-readable value[x] of an Observation-ish dict (pure fallback version)."""
    vq = obs.get("valueQuantity")
    if isinstance(vq, dict) and vq.get("value") is not None:
        unit = vq.get("unit") or vq.get("code") or ""
        return f"{vq['value']} {unit}".strip()
    for key in ("valueString", "valueInteger", "valueDecimal", "valueTime", "valueDateTime", "valuePeriod"):
        v = obs.get(key)
        if v is not None:
            return str(v)
    if isinstance(obs.get("valueBoolean"), bool):
        return "yes" if obs["valueBoolean"] else "no"
    vcc = obs.get("valueCodeableConcept")
    if isinstance(vcc, dict):
        return _cd(vcc)
    rng = obs.get("valueRange")
    if isinstance(rng, dict):
        lo = rng.get("low") if isinstance(rng.get("low"), dict) else {}
        hi = rng.get("high") if isinstance(rng.get("high"), dict) else {}
        return f"{lo.get('value')} - {hi.get('value')}".strip(" -")
    return None


def parse_observation_components(comp_list):
    """Parse Observation.component[] → ``(component_value JSON, child rows)``.

    The JSON keeps the legacy ``[{"code", "value", "unit"}]`` shape stored in
    the ``component_value`` columns; the rows feed the ``*_component`` child
    tables (FK column omitted — see module docstring).
    """
    rows = []
    for i, comp in enumerate(_as_list(comp_list)):
        if not isinstance(comp, dict):
            continue
        code_obj = comp.get("code") if isinstance(comp.get("code"), dict) else {}
        coding_list = code_obj.get("coding", []) if isinstance(code_obj, dict) else []
        loinc = None
        code_display = None
        for c in coding_list:
            if not isinstance(c, dict):
                continue
            if loinc is None and str(c.get("system", "")).endswith("loinc.org"):
                loinc = c.get("code")
            if code_display is None and c.get("display"):
                code_display = c["display"]
        if code_display is None:
            code_display = code_obj.get("text") if isinstance(code_obj, dict) else None

        vq = comp.get("valueQuantity")
        c_val, c_float, c_unit = _obs_value(comp), None, None
        if isinstance(vq, dict):
            c_unit = vq.get("unit") or vq.get("code")
            if vq.get("value") is not None:
                c_float = _float(vq["value"])

        interp = _first(comp.get("interpretation"))
        ic_code, ic_display = _cc(interp)

        rows.append(
            {
                "seq": i,
                "code_loinc": loinc,
                "code_display": code_display,
                "component_value": c_val,
                "value_float": c_float,
                "value_unit": c_unit,
                "reference_range": _ref_range_text(comp.get("referenceRange")),
                "interpretation_code": ic_code,
                "interpretation_display": ic_display,
            }
        )
    legacy = None
    if rows:
        legacy = json.dumps(
            [{"code": r["code_display"], "value": r["component_value"], "unit": r["value_unit"]} for r in rows]
        )
    return legacy, rows


# ── Encounter ──────────────────────────────────────────────────────


def participant_new_fields(part):
    """New EncounterParticipant columns (type_code, period_start, period_end) for one participant[]."""
    ptype = _first(part.get("type"))
    period = part.get("period") if isinstance(part.get("period"), dict) else {}
    return {
        "type_code": _cc(ptype)[0] if isinstance(ptype, dict) else None,
        "period_start": _dt(period.get("start")),
        "period_end": _dt(period.get("end")),
    }


def extract_encounter(res):
    rid = res.get("id")
    tcode, tdisplay = None, None
    for t in _as_list(res.get("type")):
        if isinstance(t, dict):
            tcode, tdisplay = _cc(t)
            break
    class_obj = res.get("class")
    admit_code, admit_display = _cc(res.get("admitSource"))
    dd_code, dd_display = _cc(res.get("dischargeDisposition"))
    svc = res.get("serviceType")
    account_values = []
    for ident in _as_list(res.get("identifier")):
        if isinstance(ident, dict) and ident.get("value"):
            account_values.append(str(ident["value"]))
    header = {
        "type_code": tcode,
        "type_display": tdisplay,
        "class_display": _cd(class_obj) if isinstance(class_obj, dict) else None,
        "admit_source_code": admit_code,
        "admit_source_display": admit_display,
        "discharge_disposition_code": dd_code,
        "discharge_disposition_display": dd_display,
        "service_type_display": _cd(svc) if isinstance(svc, dict) else None,
        "part_of_ref": _raw_ref(res.get("partOf")),
        "account_ids": ", ".join(dict.fromkeys(account_values)) or None,
        "accident_related": _ext_bool(
            res, "http://open.epic.com/FHIR/StructureDefinition/extension/accidentrelated"
        ),
    }
    children = {"encounter_identifier": _ident_rows(res, "encounter_id", rid)}
    return header, children


# ── DiagnosticReport / Observation ─────────────────────────────────


def extract_diagnostic_report(res):
    rid = res.get("id")
    cc_code, cc_display = _cc(res.get("conclusionCode"))
    interp = _first(res.get("interpreter"))
    pf = _first(res.get("presentedForm"))
    cat = _first(res.get("category"))
    header = {
        "conclusion_code": cc_code,
        "conclusion_code_display": cc_display,
        "interpreter_ref": _raw_ref(interp),
        "interpreter_display": _ref_display(interp),
        "presented_form_url": pf.get("url") if isinstance(pf, dict) else None,
        "presented_form_title": pf.get("title") if isinstance(pf, dict) else None,
        "presented_form_type": pf.get("contentType") if isinstance(pf, dict) else None,
        "category_code": _cc(cat)[0] if isinstance(cat, dict) else None,
    }
    children = {"diagnostic_report_identifier": _ident_rows(res, "report_id", rid)}
    return header, children


def extract_lab_result(obs):
    cat = _first(obs.get("category"))
    based_on = _first(obs.get("basedOn"))
    spec = _first(obs.get("specimen"))
    enc = _first(obs.get("encounter"))
    dar = _first(obs.get("dataAbsentReason"))
    vcc = obs.get("valueCodeableConcept")
    method = _first(obs.get("method"))
    body = _first(obs.get("bodySite"))
    vq = obs.get("valueQuantity")
    component_value, comp_rows = parse_observation_components(obs.get("component"))
    header = {
        "category_code": _cc(cat)[0] if isinstance(cat, dict) else None,
        "category_display": _cd(cat) if isinstance(cat, dict) else None,
        "based_on_ref": _raw_ref(based_on),
        "specimen_ref": _raw_ref(spec),
        "encounter_ref": _raw_ref(enc),
        "issued": _dt(obs.get("issued")),
        "note_text": _notes(obs),
        "method_display": _cd(method) if isinstance(method, dict) else None,
        "body_site": _cd(body) if isinstance(body, dict) else None,
        "data_absent_reason_code": _cc(dar)[0] if isinstance(dar, dict) else None,
        "data_absent_reason_display": _cd(dar) if isinstance(dar, dict) else None,
        "value_code": _cc(vcc)[0] if isinstance(vcc, dict) else None,
        "value_display": _cd(vcc) if isinstance(vcc, dict) else None,
        "value_comparator": vq.get("comparator") if isinstance(vq, dict) else None,
        "component_value": component_value,
    }
    children = {"lab_result_component": comp_rows}
    return header, children


def extract_clinical_observation(obs):
    cat = _first(obs.get("category"))
    vcc = obs.get("valueCodeableConcept")
    vq = obs.get("valueQuantity")
    component_value, comp_rows = parse_observation_components(obs.get("component"))
    header = {
        "category_display": _cd(cat) if isinstance(cat, dict) else None,
        "issued": _dt(obs.get("issued")),
        "note_text": _notes(obs),
        "value_code": _cc(vcc)[0] if isinstance(vcc, dict) else None,
        "value_display": _cd(vcc) if isinstance(vcc, dict) else None,
        "value_comparator": vq.get("comparator") if isinstance(vq, dict) else None,
        "component_value": component_value,
    }
    children = {"clinical_observation_component": comp_rows}
    return header, children


# ── Condition / MedicationRequest / MedicationStatement ────────────


def extract_condition(res):
    cat = _first(res.get("category"))
    code_obj = res.get("code")
    evidence = [ref for ref in (_raw_ref(e) for e in _as_list(res.get("evidence"))) if ref]
    header = {
        "category_display": _cd(cat) if isinstance(cat, dict) else None,
        "clinical_status_display": _cd(res.get("clinicalStatus")),
        "verification_status_display": _cd(res.get("verificationStatus")),
        "code_text": code_obj.get("text") if isinstance(code_obj, dict) else None,
        "evidence_refs": ", ".join(evidence) if evidence else None,
    }
    return header, {}


def extract_medication_request(res):
    rid = res.get("id")
    cat = _first(res.get("category"))
    cot = _first(res.get("courseOfTherapy"))
    disp = res.get("dispenseRequest") if isinstance(res.get("dispenseRequest"), dict) else {}
    supply = disp.get("expectedSupplyDuration") if isinstance(disp.get("expectedSupplyDuration"), dict) else None
    qty = res.get("quantity") if isinstance(res.get("quantity"), dict) else {}
    med_ref = res.get("medicationReference")
    recorder = res.get("recorder")
    prior = _first(res.get("priorPrescription"))
    group_ident = res.get("groupIdentifier") if isinstance(res.get("groupIdentifier"), dict) else None
    substitution = disp.get("substitution") if isinstance(disp.get("substitution"), dict) else None
    header = {
        "category_display": _cd(cat) if isinstance(cat, dict) else None,
        "course_of_therapy_code": _cc(cot)[0] if isinstance(cot, dict) else None,
        "course_of_therapy_display": _cd(cot) if isinstance(cot, dict) else None,
        "expected_supply_value": _float(supply.get("value")) if supply else None,
        "expected_supply_unit": (supply.get("unit") or supply.get("code")) if supply else None,
        "quantity_unit": qty.get("unit"),
        "medication_ref": _raw_ref(med_ref),
        "recorder_ref": _raw_ref(recorder),
        "recorder_display": _ref_display(recorder),
        "reported": res.get("reported") if isinstance(res.get("reported"), bool) else None,
        "prior_prescription_ref": _raw_ref(prior),
        "group_identifier_value": group_ident.get("value") if group_ident else None,
        "substitution_allowed": substitution.get("allowed") if isinstance(substitution, dict) and isinstance(substitution.get("allowed"), bool) else None,
    }
    dosage_rows = []
    for i, di in enumerate(_as_list(res.get("dosageInstruction"))):
        if not isinstance(di, dict):
            continue
        route = di.get("route") if isinstance(di.get("route"), dict) else None
        method = di.get("method") if isinstance(di.get("method"), dict) else None
        instr = di.get("patientInstruction") if isinstance(di.get("patientInstruction"), dict) else None
        timing = di.get("timing") if isinstance(di.get("timing"), dict) else None
        timing_text = timing.get("text") if timing else None
        if not timing_text and timing and timing.get("repeatDuration") is not None:
            timing_text = f"{timing['repeatDuration']} {timing.get('repeatDurationUnit') or ''}".strip()
        dose = di.get("doseQuantity") if isinstance(di.get("doseQuantity"), dict) else None
        dosage_rows.append(
            {
                "medreq_id": rid,
                "seq": i,
                "dosage_text": di.get("text"),
                "route_code": _cc(route)[0] if route else None,
                "route_display": _cd(route) if route else None,
                "method_code": _cc(method)[0] if method else None,
                "method_display": _cd(method) if method else None,
                "patient_instruction": instr.get("text") if instr else None,
                "as_needed": di.get("asNeededBoolean") if isinstance(di.get("asNeededBoolean"), bool) else None,
                "timing_text": timing_text,
                "dose_value": _float(dose.get("value")) if dose else None,
                "dose_unit": (dose.get("unit") or dose.get("code")) if dose else None,
            }
        )
    children = {
        "medication_request_identifier": _ident_rows(res, "medreq_id", rid),
        "medication_request_dosage": dosage_rows,
    }
    return header, children


def extract_medication_statement(res):
    cat = _first(res.get("category"))
    med_ref = res.get("medicationReference")
    info_src = res.get("informationSource")
    header = {
        "medication_ref": _raw_ref(med_ref),
        "reported": res.get("reported") if isinstance(res.get("reported"), bool) else None,
        "information_source_ref": _raw_ref(info_src),
        "category_code": _cc(cat)[0] if isinstance(cat, dict) else None,
    }
    return header, {}


# ── Immunization / Allergy / CareTeam ──────────────────────────────


def extract_immunization(res):
    rid = res.get("id")
    enc = _first(res.get("encounter"))
    loc = _first(res.get("location"))
    header = {
        "encounter_ref": _raw_ref(enc),  # non-column; consumer resolves to encounter_id
        "expiration_date": _date(res.get("expirationDate")),
        "location_display": loc.get("display") if isinstance(loc, dict) else None,
        "primary_source": res.get("primarySource") if isinstance(res.get("primarySource"), bool) else None,
        "report_origin_display": _cd(res.get("reportOrigin")),
    }
    children = {"immunization_identifier": _ident_rows(res, "immunization_id", rid)}
    return header, children


def extract_allergy(res):
    code_obj = res.get("code")
    parts = []
    for rx in _as_list(res.get("reaction")):
        if not isinstance(rx, dict):
            continue
        if rx.get("description"):
            parts.append(str(rx["description"]))
        for m in _as_list(rx.get("manifestation")):
            d = _cd(m) if isinstance(m, dict) else None
            if d:
                parts.append(d)
    onset = res.get("onsetDateTime")
    if not onset and isinstance(res.get("onsetPeriod"), dict):
        onset = res["onsetPeriod"].get("start")
    header = {
        "type": res.get("type") if isinstance(res.get("type"), str) else None,
        "onset_datetime": _dt(onset),
        "code_text": code_obj.get("text") if isinstance(code_obj, dict) else None,
        "reaction_description": "; ".join(parts) if parts else None,
    }
    return header, {}


def extract_care_team(res):
    rid = res.get("id")
    cat = _first(res.get("category"))
    header = {"category_code": _cc(cat)[0] if isinstance(cat, dict) else None}
    rows = []
    for i, p in enumerate(_as_list(res.get("participant"))):
        if not isinstance(p, dict):
            continue
        member = p.get("member") if isinstance(p.get("member"), dict) else None
        role = _first(p.get("role"))
        rows.append(
            {
                "care_team_id": rid,
                "seq": i,
                "member_ref": _raw_ref(member),
                "member_display": _ref_display(member) or _cd(member),
                "role_code": _cc(role)[0] if isinstance(role, dict) else None,
                "role_display": _cd(role) if isinstance(role, dict) else None,
            }
        )
    children = {"care_team_participant": rows}
    return header, children


# ── ServiceRequest / Specimen / Communication / FMH ────────────────


def extract_service_request(res):
    based = _first(res.get("basedOn"))
    code_obj = res.get("code")
    header = {
        "priority": res.get("priority") if isinstance(res.get("priority"), str) else None,
        "based_on_ref": _raw_ref(based),
        "code_text": code_obj.get("text") if isinstance(code_obj, dict) else None,
    }
    return header, {}


def extract_specimen(res):
    values = [
        str(i["value"])
        for i in _as_list(res.get("identifier"))
        if isinstance(i, dict) and i.get("value")
    ]
    header = {"identifier_value": ", ".join(dict.fromkeys(values)) or None}
    return header, {}


def extract_communication(res):
    cat = _first(res.get("category"))
    header = {"category_code": _cc(cat)[0] if isinstance(cat, dict) else None}
    return header, {}


def extract_family_member_history(res):
    rel = _first(res.get("relationship"))
    header = {"relationship_code": _cc(rel)[0] if isinstance(rel, dict) else None}
    return header, {}


# ── DocumentReference / ClinicalNote ───────────────────────────────


def _doc_context_fields(doc):
    ctx = doc.get("context") if isinstance(doc.get("context"), dict) else {}
    period = ctx.get("period") if isinstance(ctx.get("period"), dict) else {}
    subject = ctx.get("subject") if isinstance(ctx.get("subject"), dict) else None
    return {
        "context_period_start": _dt(period.get("start")),
        "context_period_end": _dt(period.get("end")),
        "subject_display": _ref_display(subject),
    }


def _author_fields(doc):
    author = _first(doc.get("author"))
    return {"author_display": _ref_display(author) if isinstance(author, dict) else None}


def extract_document_reference(doc):
    rid = doc.get("id")
    t = _first(doc.get("type"))
    cat = _first(doc.get("category"))
    authenticator = _first(doc.get("authenticator"))
    ctx = doc.get("context") if isinstance(doc.get("context"), dict) else {}
    custodian = ctx.get("custodian") if isinstance(ctx.get("custodian"), dict) else None
    header = {
        "type_code": _cc(t)[0] if isinstance(t, dict) else None,
        "doc_status": doc.get("status"),
        "authenticator_ref": _raw_ref(authenticator),
        "authenticator_display": _ref_display(authenticator),
        "custodian_display": _ref_display(custodian),
        **_doc_context_fields(doc),
        **_author_fields(doc),
        "category_code": _cc(cat)[0] if isinstance(cat, dict) else None,
    }
    content_rows = []
    for i, content in enumerate(_as_list(doc.get("content"))):
        if not isinstance(content, dict):
            continue
        att = content.get("attachment") if isinstance(content.get("attachment"), dict) else {}
        fmt = att.get("contentCoding") if isinstance(att.get("contentCoding"), dict) else None
        size = att.get("size")
        content_rows.append(
            {
                "doc_id": rid,
                "seq": i,
                "format_code": _cc(fmt)[0] if fmt else None,
                "format_display": _cd(fmt) if fmt else None,
                "attachment_url": att.get("url"),
                "attachment_title": att.get("title"),
                "attachment_type": att.get("contentType"),
                "size": size if isinstance(size, int) else None,
            }
        )
    children = {
        "document_reference_content": content_rows,
        "document_reference_identifier": _ident_rows(doc, "doc_id", rid),
    }
    return header, children


def extract_clinical_note(doc):
    """Extract from a DocumentReference resource (Epic delivers notes as DRs)."""
    rid = doc.get("id")
    t = _first(doc.get("type"))
    header = {
        "type_code": _cc(t)[0] if isinstance(t, dict) else None,
        "doc_status": doc.get("status"),
        **_doc_context_fields(doc),
        **_author_fields(doc),
    }
    children = {"clinical_note_identifier": _ident_rows(doc, "note_id", rid)}
    return header, children


# ── Session helpers (registry lookups; used by ETL + backfill) ─────


def registry_display(session, raw_ref, provider, entity_type="Patient"):
    """Look up a canonical entity reference's display in the local registry."""
    if not raw_ref:
        return None
    from myhealth_fhir.db.identity import ref_from_fhir
    from myhealth_fhir.db.models_ucla import EntityName

    canon = ref_from_fhir(raw_ref, provider, entity_type)
    if not canon:
        return None
    ent = session.get(EntityName, canon)
    if ent is None:
        return None
    return ent.name or ent.display


def upgrade_doc_displays(session, fields, doc, provider):
    """Fill subject_display / author_display from the registry when the reference
    object carried no display."""
    subj = (doc.get("context") or {}).get("subject") if isinstance(doc.get("context"), dict) else None
    if not fields.get("subject_display") and isinstance(subj, dict) and subj.get("reference"):
        fields["subject_display"] = registry_display(session, subj["reference"], provider, "Patient")
    author = _first(doc.get("author"))
    if not fields.get("author_display") and isinstance(author, dict) and author.get("reference"):
        fields["author_display"] = registry_display(session, author["reference"], provider, "Practitioner")

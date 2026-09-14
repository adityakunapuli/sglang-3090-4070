"""UCLA/Epic clinical resource persistence to the ucla DB.

Save functions moved verbatim from services/fhir_client.py (plan 4c);
they take the FHIRClient instance as ``client`` and write through the
ucla DB session.
"""

import json
import logging

from myhealth_fhir.fhir.parsing import (
    _cd,
    _coding_first_code,
    _existing_encounter_id,
    _extract_observation_value,
    _extract_patient_id,
    _parse_code_display,
    _parse_dt,
    _parse_loinc,
)

log = logging.getLogger(__name__)


def _save_actor(session, obj: dict | None, provider: str, fallback_type: str, fallback_id: str) -> str | None:
    """Persist a FHIR actor display and return its canonical reference."""
    from myhealth_fhir.db.identity import display_from_fhir, ref_from_fhir, upsert_entity_name

    if not isinstance(obj, dict):
        return None
    reference = obj.get("reference")
    ref = ref_from_fhir(reference, provider, fallback_type) if reference else None
    if ref:
        parts = ref.split(":", 2)
        return upsert_entity_name(
            session, provider=provider, entity_type=parts[1], entity_id=parts[2],
            name=display_from_fhir(obj), display=obj.get("display"),
        )
    display = display_from_fhir(obj)
    return upsert_entity_name(
        session, provider=provider, entity_type=fallback_type,
        entity_id=fallback_id, name=display, display=display,
    ) if display else None


def save_labs_to_db(client, reports: list[dict], provider: str = "ucla"):
    """Fetch child Observations for each DiagnosticReport and persist to DB.

    Returns (new_panels, new_results, total_results) counts.
    """
    from myhealth_fhir.db.engine import get_session_for
    from myhealth_fhir.db.ucla_unpack import extract_diagnostic_report, extract_lab_result
    from myhealth_fhir.models.ucla import DiagnosticReport, DiagnosticReportIdentifier, LabResult, LabResultComponent

    new_panels = 0
    new_results = 0
    skipped_obs = 0

    with get_session_for(provider) as session:
        for report in reports:
            dr_id = report.get("id", "")
            if not dr_id:
                continue

            # ── Parse DiagnosticReport ──
            code_obj = report.get("code", {})
            coding_list = code_obj.get("coding", []) if isinstance(code_obj, dict) else []

            panel_name = None
            for c in coding_list:
                if isinstance(c, dict) and c.get("display"):
                    panel_name = c["display"]
                    break
            if not panel_name:
                panel_name = code_obj.get("text") if isinstance(code_obj, dict) else None

            loinc = _parse_loinc(coding_list)

            # Effective datetime
            eff_dt = _parse_dt(
                report.get("effectiveDateTime") or (
                    report.get("effective", {}).get("value") if isinstance(report.get("effective"), dict) else None
                )
            )

            # Performer
            # Subject / patient
            subj = report.get("subject", report.get("patient", {}))
            patient_id = None
            if isinstance(subj, dict):
                ref = subj.get("reference", "")
                if "/" in ref:
                    patient_id = ref.split("/")[-1]

            # Category (could be multiple: laboratory, imaging, procedure...)
            cat_codes = []
            for c in report.get("category", []):
                if isinstance(c, dict):
                    for code in c.get("coding", []):
                        if isinstance(code, dict):
                            cat_codes.append(code.get("code", code.get("display", "")))
            category_str = ", ".join(cat_codes) if cat_codes else None

            # Body site
            body_site = None
            bs = report.get("bodySite", [])
            if bs and isinstance(bs, list):
                for b in bs:
                    if isinstance(b, dict):
                        d = b.get("coding", [{}])[0].get("display", b.get("text", ""))
                        if d:
                            body_site = d
                            break

            # Method
            method = None
            m = report.get("method", [])
            if m and isinstance(m, list):
                for mt in m:
                    if isinstance(mt, dict):
                        d = mt.get("coding", [{}])[0].get("display", mt.get("text", ""))
                        if d:
                            method = d
                            break

            # Encounter / specimen refs
            enc_id = _existing_encounter_id(session, report)
            spec_ref = ""
            if isinstance(report.get("specimen"), list) and report["specimen"]:
                spec_ref = report["specimen"][0].get("reference", "") if isinstance(report["specimen"][0], dict) else ""
            elif isinstance(report.get("specimen"), dict):
                spec_ref = report["specimen"].get("reference", "")

            # Has images (imagingResults)
            has_images = bool(report.get("imagingResults", []))

            performer_obj = report.get("performer", [{}])[0] if report.get("performer") else None
            performer_ref = _save_actor(session, performer_obj, provider, "Organization", f"report:{dr_id}:performer")

            dr_fields, dr_children = extract_diagnostic_report(report)

            # Upsert panel
            existing = session.get(DiagnosticReport, dr_id)
            if existing:
                existing.status = report.get("status")
                existing.category = category_str
                existing.code_display = panel_name
                existing.code_text = code_obj.get("text") if isinstance(code_obj, dict) else None
                existing.code_loinc = loinc
                existing.effective_datetime = eff_dt
                existing.issued = _parse_dt(report.get("issued"))
                existing.performer_ref = performer_ref
                existing.conclusion = report.get("conclusion")
                existing.body_site = body_site
                existing.method = method
                existing.encounter_id = enc_id
                existing.specimen_ref = spec_ref
                existing.has_images = has_images
                for k, v in dr_fields.items():
                    setattr(existing, k, v)
                existing.raw_json = json.dumps(report)
                # Delete old results (will be re-inserted)
                for r in existing.results:
                    session.delete(r)
                session.flush()
            else:
                dr = DiagnosticReport(
                    id=dr_id,
                    patient_id=patient_id,
                    provider=provider,
                    status=report.get("status"),
                    category=category_str,
                    code_display=panel_name,
                    code_text=code_obj.get("text") if isinstance(code_obj, dict) else None,
                    code_loinc=loinc,
                    effective_datetime=eff_dt,
                    issued=_parse_dt(report.get("issued")),
                    performer_ref=performer_ref,
                    conclusion=report.get("conclusion"),
                    body_site=body_site,
                    method=method,
                    encounter_id=enc_id,
                    specimen_ref=spec_ref,
                    has_images=has_images,
                    raw_json=json.dumps(report),
                    **dr_fields,
                )
                session.add(dr)
                new_panels += 1
                session.flush()

            session.query(DiagnosticReportIdentifier).filter(DiagnosticReportIdentifier.report_id == dr_id).delete(synchronize_session=False)
            for row in dr_children.get("diagnostic_report_identifier", []):
                session.add(DiagnosticReportIdentifier(**row))

            # ── Fetch and save child Observations ──
            result_refs = report.get("result", [])
            for ref in result_refs:
                ref_str = ref.get("reference", "") if isinstance(ref, dict) else str(ref)
                obs_id = ref_str.split("/")[-1] if "/" in ref_str else ref_str
                try:
                    obs = client.get("Observation", obs_id, patient_id=patient_id)
                except Exception:
                    skipped_obs += 1
                    continue

                # Delete existing row with same fhir_id (upsert pattern)
                old_obs = session.query(LabResult).filter(LabResult.fhir_id == obs_id).first()
                if old_obs:
                    session.delete(old_obs)
                    session.flush()
                else:
                    new_results += 1

                # Parse observation
                obs_code = obs.get("code", {})
                obs_coding = obs_code.get("coding", []) if isinstance(obs_code, dict) else []
                obs_loinc = _parse_loinc(obs_coding)
                obs_name = _parse_code_display(obs_code)
                obs_text = obs_code.get("text") if isinstance(obs_code, dict) else None

                # Value extraction
                value_str = _extract_observation_value(obs)
                value_float = None
                value_unit = None

                val_qty = obs.get("valueQuantity")
                if isinstance(val_qty, dict):
                    num = val_qty.get("value")
                    if num is not None:
                        try:
                            value_float = float(num)
                        except (TypeError, ValueError):
                            value_float = None
                        value_unit = val_qty.get("unit", "")

                if value_str is None:
                    value_str = "(no value)"

                # Reference range
                ref_ranges = obs.get("referenceRange", [])
                range_parts = []
                for r in ref_ranges:
                    txt = r.get("text")
                    if txt:
                        range_parts.append(txt)
                    else:
                        low = r.get("low", {})
                        high = r.get("high", {})
                        lv = low.get("value") if isinstance(low, dict) else None
                        hv = high.get("value") if isinstance(high, dict) else None
                        lu = low.get("unit", "") if isinstance(low, dict) else ""
                        if lv is not None and hv is not None:
                            range_parts.append(f"{lv}-{hv} {lu}".strip())
                        elif lv is not None:
                            range_parts.append(f">={lv} {lu}".strip())
                        elif hv is not None:
                            hu = high.get("unit", "") if isinstance(high, dict) else ""
                            range_parts.append(f"<={hv} {hu}".strip())
                ref_range_str = ", ".join(range_parts) if range_parts else None

                # Interpretation
                interp_list = obs.get("interpretation", [])
                interp_code = None
                interp_display = None
                if interp_list:
                    for i in interp_list:
                        if isinstance(i, dict):
                            cc = i.get("coding", [])
                            if cc and isinstance(cc[0], dict):
                                interp_code = cc[0].get("code")
                                interp_display = cc[0].get("display")
                                break

                obs_eff = _parse_dt(obs.get("effectiveDateTime"))
                lr_fields, lr_children = extract_lab_result(obs)

                lr = LabResult(
                    fhir_id=obs_id,
                    report_id=dr_id,
                    code_display=obs_name,
                    code_text=obs_text,
                    code_loinc=obs_loinc,
                    value=value_str,
                    value_float=value_float,
                    value_unit=value_unit,
                    reference_range=ref_range_str,
                    interpretation_code=interp_code,
                    interpretation_display=interp_display,
                    effective_datetime=obs_eff,
                    status=obs.get("status"),
                    raw_json=json.dumps(obs),
                    **lr_fields,
                )
                session.add(lr)
                session.flush()
                session.query(LabResultComponent).filter(LabResultComponent.lab_id == lr.id).delete(synchronize_session=False)
                for row in lr_children.get("lab_result_component", []):
                    session.add(LabResultComponent(lab_id=lr.id, **row))

            if len(reports) > 0:
                pass  # commit at end

        session.commit()

    return new_panels, new_results, skipped_obs


def save_imaging_observations(client, observations: list[dict], provider: str = "ucla"):
    """Save standalone imaging observations (POCUS, ultrasound, etc.) to DB."""
    from myhealth_fhir.db.engine import get_session_for
    from myhealth_fhir.models.ucla import ImagingObservation

    with get_session_for(provider) as session:
        for obs in observations:
            obs_id = obs.get("id", "")
            if not obs_id:
                continue

            # Delete existing
            old = session.query(ImagingObservation).filter(ImagingObservation.fhir_id == obs_id).first()
            if old:
                session.delete(old)
                session.flush()

            # Parse
            obs_code = obs.get("code", {})
            obs_coding = obs_code.get("coding", []) if isinstance(obs_code, dict) else []
            obs_loinc = _parse_loinc(obs_coding)
            obs_name = _parse_code_display(obs_code)
            obs_text = obs_code.get("text") if isinstance(obs_code, dict) else None

            # Value
            value_str = _extract_observation_value(obs)
            value_float = None
            value_unit = None
            val_qty = obs.get("valueQuantity")
            if isinstance(val_qty, dict):
                num = val_qty.get("value")
                if num is not None:
                    try:
                        value_float = float(num)
                    except (TypeError, ValueError):
                        value_float = None
                    value_unit = val_qty.get("unit", "")

            # Ref range
            ref_ranges = obs.get("referenceRange", [])
            range_parts = []
            for r in ref_ranges:
                txt = r.get("text")
                if txt:
                    range_parts.append(txt)
            ref_range_str = ", ".join(range_parts) if range_parts else None

            # Interpretation
            interp_list = obs.get("interpretation", [])
            interp_code = None
            interp_display = None
            if interp_list:
                for i in interp_list:
                    if isinstance(i, dict):
                        cc = i.get("coding", [])
                        if cc and isinstance(cc[0], dict):
                            interp_code = cc[0].get("code")
                            interp_display = cc[0].get("display")
                            break

            # Components
            components = obs.get("component", [])
            component_value = None
            if components:
                parsed = []
                for comp in components:
                    if not isinstance(comp, dict):
                        continue
                    c_code = comp.get("code", {})
                    c_name = _parse_code_display(c_code) if isinstance(c_code, dict) else None
                    c_val = _extract_observation_value(comp)
                    c_unit = None
                    cq = comp.get("valueQuantity")
                    if isinstance(cq, dict):
                        c_unit = cq.get("unit", cq.get("code"))
                    parsed.append({"code": c_name, "value": c_val, "unit": c_unit})
                if parsed:
                    component_value = json.dumps(parsed)

            io = ImagingObservation(
                fhir_id=obs_id,
                code_display=obs_name,
                code_text=obs_text,
                code_loinc=obs_loinc,
                value=value_str or "",
                value_float=value_float,
                value_unit=value_unit,
                reference_range=ref_range_str,
                interpretation_code=interp_code,
                interpretation_display=interp_display,
                effective_datetime=_parse_dt(obs.get("effectiveDateTime")),
                status=obs.get("status"),
                component_value=component_value,
                raw_json=json.dumps(obs),
            )
            session.add(io)
        session.commit()


# ── Encounter ──────────────────────────────────────────────────────


def save_encounters_to_db(resources: list[dict], provider: str = "ucla") -> tuple[int, int]:
    """Upsert FHIR Encounter rows into the ucla DB by resource id; returns (inserted, updated)."""
    from myhealth_fhir.db.engine import get_session_for
    from myhealth_fhir.db.ucla_unpack import extract_encounter, participant_new_fields
    from myhealth_fhir.models.ucla import Encounter, EncounterIdentifier, EncounterParticipant

    inserted = 0
    updated = 0
    with get_session_for(provider) as session:
        for res in resources:
            eid = res.get("id", "")
            if not eid:
                continue
            period = res.get("period", {})
            class_obj = res.get("class", {})
            reason = res.get("reasonCode", [])
            reason_display = ""
            if reason and isinstance(reason[0], dict):
                reason_display = _cd(reason[0])

            location = ""
            for loc in res.get("location", []):
                if isinstance(loc, dict):
                    loc_disp = loc.get("location", {}).get("display", "")
                    if loc_disp:
                        location = loc_disp
                        break

            existing = session.get(Encounter, eid)
            patient_id = _extract_patient_id(res)
            from myhealth_fhir.db.identity import ref_from_fhir, upsert_patient_name
            subject = res.get("subject") if isinstance(res.get("subject"), dict) else None
            patient_ref = ref_from_fhir(subject.get("reference") if subject else None, provider, "Patient")
            if patient_ref and subject:
                upsert_patient_name(session, provider=provider, patient_id=patient_ref.rsplit(":", 1)[-1], name=subject.get("display"))
            enc_fields, enc_children = extract_encounter(res)
            if existing:
                updated += 1
                existing.patient_id = patient_id
                existing.patient_ref = patient_ref
                existing.status = res.get("status")
                existing.class_ = class_obj.get("code") if isinstance(class_obj, dict) else None
                existing.period_start = _parse_dt(period.get("start"))
                existing.period_end = _parse_dt(period.get("end"))
                existing.reason_display = reason_display
                existing.location = location
                for k, v in enc_fields.items():
                    setattr(existing, k, v)
                existing.raw_json = json.dumps(res)
            else:
                enc = Encounter(
                    id=eid,
                    patient_id=patient_id,
                    patient_ref=patient_ref,
                    status=res.get("status"),
                    class_=class_obj.get("code") if isinstance(class_obj, dict) else None,
                    period_start=_parse_dt(period.get("start")),
                    period_end=_parse_dt(period.get("end")),
                    reason_display=reason_display,
                    location=location,
                    raw_json=json.dumps(res),
                    **enc_fields,
                )
                session.add(enc)
                inserted += 1
            session.flush()

            session.query(EncounterIdentifier).filter(EncounterIdentifier.encounter_id == eid).delete(synchronize_session=False)
            for row in enc_children.get("encounter_identifier", []):
                session.add(EncounterIdentifier(**row))

            # Participants
            for p_old in session.query(EncounterParticipant).filter(EncounterParticipant.encounter_id == eid).all():
                session.delete(p_old)
            session.flush()
            for part in res.get("participant", []):
                if isinstance(part, dict):
                    ind = part.get("individual", {})
                    role = part.get("role", [{}])[0] if part.get("role") else {}
                    session.add(EncounterParticipant(
                        encounter_id=eid,
                        individual_ref=_save_actor(session, ind, provider, "Practitioner", f"encounter:{eid}:participant"),
                        role_code=role.get("coding", [{}])[0].get("code") if role.get("coding") else None,
                        role_display=role.get("text", _cd(role)),
                        **participant_new_fields(part),
                    ))
        session.commit()
    return (inserted, updated)


# ── Condition ──────────────────────────────────────────────────────


def save_conditions_to_db(resources: list[dict], provider: str = "ucla") -> tuple[int, int]:
    """Upsert FHIR Condition rows into the ucla DB by resource id; returns (inserted, updated)."""
    from myhealth_fhir.db.engine import get_session_for
    from myhealth_fhir.db.ucla_unpack import extract_condition
    from myhealth_fhir.models.ucla import Condition

    inserted = 0
    updated = 0
    with get_session_for(provider) as session:
        for r in resources:
            rid = r.get("id", "")
            if not rid:
                continue
            code_obj = r.get("code", {})
            code_info = _coding_first_code(code_obj)
            body_site = ""
            for bs in r.get("bodySite", []):
                if isinstance(bs, dict):
                    body_site = _cd(bs)
                    break
            note_text = ""
            for n in r.get("note", []):
                if isinstance(n, dict):
                    note_text = (note_text + "; " + n.get("text", "")) if note_text else n.get("text", "")

            asserter_ref = _save_actor(session, r.get("asserter"), provider, "Practitioner", rid)
            cond_fields, _ = extract_condition(r)

            existing = session.get(Condition, rid)
            if existing:
                updated += 1
                for k, v in {"clinical_status": r.get("clinicalStatus", {}).get("coding", [{}])[0].get("code") if isinstance(r.get("clinicalStatus"), dict) else None,
                             "verification_status": r.get("verificationStatus", {}).get("coding", [{}])[0].get("code") if isinstance(r.get("verificationStatus"), dict) else None,
                             "code_display": code_info.get("display", _cd(code_obj)),
                              "asserter_ref": asserter_ref,
                             "onset_datetime": _parse_dt(r.get("onsetDateTime")),
                             "abatement_datetime": _parse_dt(r.get("abatementDateTime"))}.items():
                    setattr(existing, k, v)
                for k, v in cond_fields.items():
                    setattr(existing, k, v)
                existing.raw_json = json.dumps(r)
            else:
                session.add(Condition(
                    fhir_id=rid, patient_id=_extract_patient_id(r),
                    encounter_id=_existing_encounter_id(session, r),
                    clinical_status=r.get("clinicalStatus", {}).get("coding", [{}])[0].get("code") if isinstance(r.get("clinicalStatus"), dict) else None,
                    verification_status=r.get("verificationStatus", {}).get("coding", [{}])[0].get("code") if isinstance(r.get("verificationStatus"), dict) else None,
                    category=r.get("category", [{}])[0].get("coding", [{}])[0].get("code") if r.get("category") else None,
                    code_system=code_info.get("system"),
                    code_value=code_info.get("code"),
                    code_display=code_info.get("display", _cd(code_obj)),
                    body_site=body_site, severity_text=r.get("severity", {}).get("coding", [{}])[0].get("display") if isinstance(r.get("severity"), dict) else None,
                    onset_datetime=_parse_dt(r.get("onsetDateTime")),
                    abatement_datetime=_parse_dt(r.get("abatementDateTime")),
                    recorded_date=_parse_dt(r.get("recordedDate")),
                    asserter_ref=asserter_ref,
                    note_text=note_text, raw_json=json.dumps(r),
                    **cond_fields,
                ))
                inserted += 1
        session.commit()
    return (inserted, updated)


# ── Procedure ──────────────────────────────────────────────────────


def save_procedures_to_db(resources: list[dict], provider: str = "ucla") -> tuple[int, int]:
    """Upsert FHIR Procedure rows into the ucla DB by resource id; returns (inserted, updated)."""
    from myhealth_fhir.db.engine import get_session_for
    from myhealth_fhir.models.ucla import ProcedureRecord

    inserted = 0
    updated = 0
    with get_session_for(provider) as session:
        for r in resources:
            rid = r.get("id", "")
            if not rid:
                continue
            code_obj = r.get("code", {})
            code_info = _coding_first_code(code_obj)
            performer_obj = None
            for p in r.get("performer", []):
                if isinstance(p, dict):
                    act = p.get("actor", {})
                    performer_obj = act
            body_site = ""
            for bs in r.get("bodySite", []):
                if isinstance(bs, dict):
                    body_site = _cd(bs)
                    break
            note_text = ""
            for n in r.get("note", []):
                if isinstance(n, dict):
                    note_text = (note_text + "; " + n.get("text", "")) if note_text else n.get("text", "")

            performer_ref = _save_actor(session, performer_obj, provider, "Practitioner", rid)

            existing = session.get(ProcedureRecord, rid)
            if existing:
                updated += 1
                existing.status = r.get("status")
                existing.code_display = code_info.get("display", _cd(code_obj))
                existing.performed_datetime = _parse_dt(r.get("performedDateTime"))
                existing.performer_ref = performer_ref
                existing.raw_json = json.dumps(r)
            else:
                session.add(ProcedureRecord(
                    fhir_id=rid, patient_id=_extract_patient_id(r),
                    encounter_id=_existing_encounter_id(session, r),
                    status=r.get("status"), category=r.get("category", {}).get("coding", [{}])[0].get("display") if isinstance(r.get("category"), dict) else None,
                    code_system=code_info.get("system"), code_value=code_info.get("code"),
                    code_display=code_info.get("display", _cd(code_obj)),
                    performed_datetime=_parse_dt(r.get("performedDateTime")),
                    performer_ref=performer_ref, location=r.get("location", {}).get("display") if isinstance(r.get("location"), dict) else None,
                    reason_display=r.get("reasonCode", [{}])[0].get("coding", [{}])[0].get("display") if r.get("reasonCode") else None,
                    outcome_text=r.get("outcome"), body_site=body_site, note_text=note_text,
                    raw_json=json.dumps(r),
                ))
                inserted += 1
        session.commit()
    return (inserted, updated)


# ── MedicationStatement ────────────────────────────────────────────


def save_medication_statements_to_db(resources: list[dict], provider: str = "ucla") -> tuple[int, int]:
    """Upsert FHIR MedicationStatement rows into the ucla DB by resource id; returns (inserted, updated)."""
    from myhealth_fhir.db.engine import get_session_for
    from myhealth_fhir.db.ucla_unpack import extract_medication_statement
    from myhealth_fhir.models.ucla import MedicationStatement

    inserted = 0
    updated = 0
    with get_session_for(provider) as session:
        for r in resources:
            rid = r.get("id", "")
            if not rid:
                continue
            med = r.get("medicationCodeableConcept", {})
            med_info = _coding_first_code(med)
            eff = r.get("effectivePeriod", {})
            dosages = r.get("dosage", [])
            dosage_text = dosages[0].get("text", "") if dosages and isinstance(dosages[0], dict) else ""
            route = dosages[0].get("route", {}) if dosages and isinstance(dosages[0], dict) else {}
            note_text = ""
            for n in r.get("note", []):
                if isinstance(n, dict):
                    note_text = (note_text + "; " + n.get("text", "")) if note_text else n.get("text", "")

            ms_fields, _ = extract_medication_statement(r)
            existing = session.get(MedicationStatement, rid)
            if existing:
                updated += 1
                existing.status = r.get("status")
                existing.medication_display = med_info.get("display", _cd(med))
                for k, v in ms_fields.items():
                    setattr(existing, k, v)
                existing.raw_json = json.dumps(r)
            else:
                session.add(MedicationStatement(
                    fhir_id=rid, patient_id=_extract_patient_id(r),
                    status=r.get("status"), category=r.get("category", {}).get("coding", [{}])[0].get("display") if r.get("category") else None,
                    medication_display=med_info.get("display", _cd(med)),
                    medication_code=med_info.get("code"), medication_system=med_info.get("system"),
                    effective_start=_parse_dt(eff.get("start")), effective_end=_parse_dt(eff.get("end")),
                    date_asserted=_parse_dt(r.get("dateAsserted")),
                    information_source=r.get("informationSource", {}).get("display") if isinstance(r.get("informationSource"), dict) else None,
                    reason_display=r.get("reasonCode", [{}])[0].get("coding", [{}])[0].get("display") if r.get("reasonCode") else None,
                    dosage_text=dosage_text, route_display=route.get("coding", [{}])[0].get("display") if route.get("coding") else None,
                    note_text=note_text, raw_json=json.dumps(r),
                    **ms_fields,
                ))
                inserted += 1
        session.commit()
    return (inserted, updated)


# ── MedicationRequest ──────────────────────────────────────────────


def save_medication_requests_to_db(resources: list[dict], provider: str = "ucla") -> tuple[int, int]:
    """Upsert FHIR MedicationRequest rows into the ucla DB by resource id; returns (inserted, updated)."""
    from myhealth_fhir.db.engine import get_session_for
    from myhealth_fhir.db.ucla_unpack import extract_medication_request
    from myhealth_fhir.models.ucla import MedicationRequest, MedicationRequestDosage, MedicationRequestIdentifier

    inserted = 0
    updated = 0
    with get_session_for(provider) as session:
        for r in resources:
            rid = r.get("id", "")
            if not rid:
                continue
            med = r.get("medicationCodeableConcept", {})
            med_info = _coding_first_code(med)
            dispense = r.get("dispenseRequest", {})
            validity = dispense.get("validityPeriod", {}) if isinstance(dispense, dict) else {}
            dosages = r.get("dosageInstruction", [])
            di_text = dosages[0].get("text", "") if dosages and isinstance(dosages[0], dict) else ""
            note_text = ""
            for n in r.get("note", []):
                if isinstance(n, dict):
                    note_text = (note_text + "; " + n.get("text", "")) if note_text else n.get("text", "")

            requester_ref = _save_actor(session, r.get("requester"), provider, "Practitioner", rid)
            mr_fields, mr_children = extract_medication_request(r)
            existing = session.get(MedicationRequest, rid)
            if existing:
                updated += 1
                existing.status = r.get("status")
                existing.medication_display = med_info.get("display", _cd(med))
                existing.requester_ref = requester_ref
                for k, v in mr_fields.items():
                    setattr(existing, k, v)
                existing.raw_json = json.dumps(r)
            else:
                session.add(MedicationRequest(
                    fhir_id=rid, patient_id=_extract_patient_id(r),
                    encounter_id=_existing_encounter_id(session, r),
                    status=r.get("status"), intent=r.get("intent"),
                    medication_display=med_info.get("display", _cd(med)),
                    medication_code=med_info.get("code"), medication_system=med_info.get("system"),
                    authored_on=_parse_dt(r.get("authoredOn")),
                    requester_ref=requester_ref,
                    dosage_instruction=di_text,
                    quantity_dispensed=dispense.get("quantity", {}).get("value") if isinstance(dispense, dict) else None,
                    refills=dispense.get("numberOfRepeatsAllowed") if isinstance(dispense, dict) else None,
                    validity_start=_parse_dt(validity.get("start")), validity_end=_parse_dt(validity.get("end")),
                    reason_display=r.get("reasonCode", [{}])[0].get("coding", [{}])[0].get("display") if r.get("reasonCode") else None,
                    note_text=note_text, raw_json=json.dumps(r),
                    **mr_fields,
                ))
                inserted += 1
            session.flush()
            session.query(MedicationRequestIdentifier).filter(MedicationRequestIdentifier.medreq_id == rid).delete(synchronize_session=False)
            for row in mr_children.get("medication_request_identifier", []):
                session.add(MedicationRequestIdentifier(**row))
            session.query(MedicationRequestDosage).filter(MedicationRequestDosage.medreq_id == rid).delete(synchronize_session=False)
            for row in mr_children.get("medication_request_dosage", []):
                session.add(MedicationRequestDosage(**row))
        session.commit()
    return (inserted, updated)


# ── AllergyIntolerance ─────────────────────────────────────────────


def save_allergies_to_db(resources: list[dict], provider: str = "ucla") -> tuple[int, int]:
    """Upsert FHIR AllergyIntolerance rows into the ucla DB by resource id; returns (inserted, updated)."""
    from myhealth_fhir.db.engine import get_session_for
    from myhealth_fhir.db.ucla_unpack import extract_allergy
    from myhealth_fhir.models.ucla import AllergyIntolerance

    inserted = 0
    updated = 0
    with get_session_for(provider) as session:
        for r in resources:
            rid = r.get("id", "")
            if not rid:
                continue
            code_obj = r.get("code", {})
            code_info = _coding_first_code(code_obj)
            reactions = r.get("reaction", [])
            manifestation = ""
            severity = ""
            for rx in reactions:
                if isinstance(rx, dict):
                    for m in rx.get("manifestation", []):
                        if isinstance(m, dict):
                            m_text = _cd(m) or m.get("text", "")
                            manifestation = (manifestation + ", " + m_text) if manifestation else m_text
                    severity = rx.get("severity", severity)
            note_text = ""
            for n in r.get("note", []):
                if isinstance(n, dict):
                    note_text = (note_text + "; " + n.get("text", "")) if note_text else n.get("text", "")

            recorder_ref = _save_actor(session, r.get("recorder"), provider, "Practitioner", rid)
            al_fields, _ = extract_allergy(r)
            existing = session.get(AllergyIntolerance, rid)
            if existing:
                updated += 1
                existing.clinical_status = r.get("clinicalStatus", {}).get("coding", [{}])[0].get("code") if isinstance(r.get("clinicalStatus"), dict) else None
                existing.code_display = code_info.get("display", _cd(code_obj))
                existing.recorder_ref = recorder_ref
                for k, v in al_fields.items():
                    setattr(existing, k, v)
                existing.raw_json = json.dumps(r)
            else:
                session.add(AllergyIntolerance(
                    fhir_id=rid, patient_id=_extract_patient_id(r),
                    clinical_status=r.get("clinicalStatus", {}).get("coding", [{}])[0].get("code") if isinstance(r.get("clinicalStatus"), dict) else None,
                    verification_status=r.get("verificationStatus", {}).get("coding", [{}])[0].get("code") if isinstance(r.get("verificationStatus"), dict) else None,
                    category=r.get("category"), criticality=r.get("criticality"),
                    code_display=code_info.get("display", _cd(code_obj)),
                    code_system=code_info.get("system"), code_value=code_info.get("code"),
                    reaction_manifestation=manifestation, reaction_severity=severity,
                    recorded_date=_parse_dt(r.get("recordedDate")),
                    recorder_ref=recorder_ref,
                    note_text=note_text, raw_json=json.dumps(r),
                    **al_fields,
                ))
                inserted += 1
        session.commit()
    return (inserted, updated)


# ── Immunization ───────────────────────────────────────────────────


def save_immunizations_to_db(resources: list[dict], provider: str = "ucla") -> tuple[int, int]:
    """Upsert FHIR Immunization rows into the ucla DB by resource id; returns (inserted, updated)."""
    from myhealth_fhir.db.engine import get_session_for
    from myhealth_fhir.db.ucla_unpack import extract_immunization
    from myhealth_fhir.models.ucla import Immunization, ImmunizationIdentifier

    inserted = 0
    updated = 0
    with get_session_for(provider) as session:
        for r in resources:
            rid = r.get("id", "")
            if not rid:
                continue
            vac = r.get("vaccineCode", {})
            vac_info = _coding_first_code(vac)
            note_text = ""
            for n in r.get("note", []):
                if isinstance(n, dict):
                    note_text = (note_text + "; " + n.get("text", "")) if note_text else n.get("text", "")
            dose_qty = r.get("doseQuantity", {})
            performer_obj = None
            for p in r.get("performer", []):
                if isinstance(p, dict):
                    act = p.get("actor", {})
                    performer_obj = act

            performer_ref = _save_actor(session, performer_obj, provider, "Practitioner", rid)
            im_fields, im_children = extract_immunization(r)
            enc_ref = im_fields.pop("encounter_ref", None)
            enc_id = _existing_encounter_id(session, {"encounter": {"reference": enc_ref}}) if enc_ref else None
            existing = session.get(Immunization, rid)
            if existing:
                updated += 1
                existing.status = r.get("status")
                existing.vaccine_display = vac_info.get("display", _cd(vac))
                existing.performer_ref = performer_ref
                existing.encounter_id = enc_id
                for k, v in im_fields.items():
                    setattr(existing, k, v)
                existing.raw_json = json.dumps(r)
            else:
                session.add(Immunization(
                    fhir_id=rid, patient_id=_extract_patient_id(r),
                    status=r.get("status"),
                    vaccine_display=vac_info.get("display", _cd(vac)),
                    vaccine_code=vac_info.get("code"), vaccine_system=vac_info.get("system"),
                    occurrence_datetime=_parse_dt(r.get("occurrenceDateTime")),
                    manufacturer=r.get("manufacturer", {}).get("display") if isinstance(r.get("manufacturer"), dict) else None,
                    lot_number=r.get("lotNumber"),
                    dose_quantity=dose_qty.get("value") if isinstance(dose_qty, dict) else None,
                    dose_unit=dose_qty.get("unit") if isinstance(dose_qty, dict) else None,
                    route_display=r.get("route", {}).get("coding", [{}])[0].get("display") if isinstance(r.get("route"), dict) else None,
                    site_display=r.get("site", {}).get("coding", [{}])[0].get("display") if isinstance(r.get("site"), dict) else None,
                    performer_ref=performer_ref,
                    encounter_id=enc_id,
                    reason_code=r.get("reasonCode", [{}])[0].get("coding", [{}])[0].get("display") if r.get("reasonCode") else None,
                    note_text=note_text, raw_json=json.dumps(r),
                    **im_fields,
                ))
                inserted += 1
            session.query(ImmunizationIdentifier).filter(ImmunizationIdentifier.immunization_id == rid).delete(synchronize_session=False)
            for row in im_children.get("immunization_identifier", []):
                session.add(ImmunizationIdentifier(**row))
        session.commit()
    return (inserted, updated)


# ── CarePlan ───────────────────────────────────────────────────────


def save_care_plans_to_db(resources: list[dict], provider: str = "ucla") -> tuple[int, int]:
    """Upsert FHIR CarePlan rows into the ucla DB by resource id; returns (inserted, updated)."""
    from myhealth_fhir.db.engine import get_session_for
    from myhealth_fhir.models.ucla import CarePlan

    inserted = 0
    updated = 0
    with get_session_for(provider) as session:
        for r in resources:
            rid = r.get("id", "")
            if not rid:
                continue
            goals = []
            for g in r.get("goal", []):
                if isinstance(g, dict):
                    goals.append(g.get("description", {}).get("text", ""))
            activities = []
            for a in r.get("activity", []):
                if isinstance(a, dict):
                    detail = a.get("detail", {})
                    if isinstance(detail, dict):
                        activities.append(detail.get("description", ""))
            note_text = ""
            for n in r.get("note", []):
                if isinstance(n, dict):
                    note_text = (note_text + "; " + n.get("text", "")) if note_text else n.get("text", "")

            author_ref = _save_actor(session, r.get("author"), provider, "Practitioner", rid)
            existing = session.get(CarePlan, rid)
            if existing:
                updated += 1
                existing.status = r.get("status")
                existing.title = r.get("title")
                existing.author_ref = author_ref
                existing.raw_json = json.dumps(r)
            else:
                session.add(CarePlan(
                    fhir_id=rid, patient_id=_extract_patient_id(r),
                    encounter_id=_existing_encounter_id(session, r),
                    status=r.get("status"), intent=r.get("intent"),
                    category=r.get("category", [{}])[0].get("coding", [{}])[0].get("display") if r.get("category") else None,
                    title=r.get("title"), description=r.get("description"),
                    period_start=_parse_dt(r.get("period", {}).get("start")),
                    period_end=_parse_dt(r.get("period", {}).get("end")),
                    author_ref=author_ref,
                    goal_descriptions="; ".join(g for g in goals if g) if goals else None,
                    activity_text="; ".join(a for a in activities if a) if activities else None,
                    note_text=note_text, raw_json=json.dumps(r),
                ))
                inserted += 1
        session.commit()
    return (inserted, updated)


# ── MedicationAdministration (MAR) ─────────────────────────────────


def save_medication_administrations_to_db(resources: list[dict], provider: str = "ucla") -> tuple[int, int]:
    """Upsert FHIR MedicationAdministration rows into the ucla DB by resource id; returns (inserted, updated)."""
    from myhealth_fhir.db.engine import get_session_for
    from myhealth_fhir.models.ucla import MedicationAdministration

    inserted = 0
    updated = 0
    with get_session_for(provider) as session:
        for r in resources:
            rid = r.get("id", "")
            if not rid:
                continue
            med = r.get("medicationCodeableConcept", r.get("medicationReference", {}))
            med_info = _coding_first_code(med)
            dosage = r.get("dosage", {}) if isinstance(r.get("dosage"), dict) else {}
            route_display = None
            route = dosage.get("route", {})
            if isinstance(route, dict):
                route_display = _cd(route)
            dose_text = None
            dose_qty = dosage.get("dose", {})
            if isinstance(dose_qty, dict):
                dv = dose_qty.get("value")
                if dv is not None:
                    du = dose_qty.get("unit", "") or ""
                    dose_text = f"{dv} {du}".strip()
            performer_ref = None
            performer = r.get("performer", [{}])[0] if r.get("performer") else None
            if isinstance(performer, dict):
                performer_ref = _save_actor(session, performer.get("actor"), provider, "Practitioner", rid)
            note_text = ""
            for n in r.get("note", []):
                if isinstance(n, dict):
                    note_text = (note_text + "; " + n.get("text", "")) if note_text else n.get("text", "")

            existing = session.get(MedicationAdministration, rid)
            if existing:
                updated += 1
                existing.status = r.get("status")
                existing.medication_display = med_info.get("display", _cd(med))
                existing.raw_json = json.dumps(r)
            else:
                session.add(MedicationAdministration(
                    fhir_id=rid, source="fhir", patient_id=_extract_patient_id(r),
                    encounter_id=_existing_encounter_id(session, r),
                    status=r.get("status"),
                    medication_display=med_info.get("display", _cd(med)),
                    administered_datetime=_parse_dt(r.get("effectiveDateTime") or (
                        r.get("effective", {}).get("value") if isinstance(r.get("effective"), dict) else None
                    )),
                    route_display=route_display, dose_display=dose_text,
                    performer_ref=performer_ref, note_text=note_text, raw_json=json.dumps(r),
                ))
                inserted += 1
        session.commit()
    return (inserted, updated)


# ── ServiceRequest (orders) ────────────────────────────────────────


def save_service_requests_to_db(resources: list[dict], provider: str = "ucla") -> tuple[int, int]:
    """Upsert FHIR ServiceRequest rows into the ucla DB by resource id; returns (inserted, updated)."""
    from myhealth_fhir.db.engine import get_session_for
    from myhealth_fhir.db.ucla_unpack import extract_service_request
    from myhealth_fhir.models.ucla import ServiceRequest

    inserted = 0
    updated = 0
    with get_session_for(provider) as session:
        for r in resources:
            rid = r.get("id", "")
            if not rid:
                continue
            code_obj = r.get("code", {})
            code_info = _coding_first_code(code_obj)
            note_text = ""
            for n in r.get("note", []):
                if isinstance(n, dict):
                    note_text = (note_text + "; " + n.get("text", "")) if note_text else n.get("text", "")
            requester_ref = _save_actor(session, r.get("requester"), provider, "Practitioner", rid)
            sr_fields, _ = extract_service_request(r)
            cat = r.get("category", [{}])[0] if r.get("category") else {}
            existing = session.get(ServiceRequest, rid)
            if existing:
                updated += 1
                existing.status = r.get("status")
                existing.code_display = code_info.get("display", _cd(code_obj))
                existing.note_text = note_text or existing.note_text
                for k, v in sr_fields.items():
                    setattr(existing, k, v)
                existing.raw_json = json.dumps(r)
            else:
                session.add(ServiceRequest(
                    fhir_id=rid, source="fhir", patient_id=_extract_patient_id(r),
                    encounter_id=_existing_encounter_id(session, r),
                    status=r.get("status"), intent=r.get("intent"),
                    category=_cd(cat) if isinstance(cat, dict) else None,
                    code_display=code_info.get("display", _cd(code_obj)),
                    code_system=code_info.get("system"), code_value=code_info.get("code"),
                    authored_on=_parse_dt(r.get("authoredOn")),
                    requester_ref=requester_ref,
                    reason_display=r.get("reasonCode", [{}])[0].get("coding", [{}])[0].get("display") if r.get("reasonCode") else None,
                    order_detail=r.get("orderDetail", [{}])[0].get("text") if r.get("orderDetail") else None,
                    note_text=note_text, raw_json=json.dumps(r),
                    **sr_fields,
                ))
                inserted += 1
        session.commit()
    return (inserted, updated)


# ── Specimen ───────────────────────────────────────────────────────


def save_specimens_to_db(resources: list[dict], provider: str = "ucla") -> tuple[int, int]:
    """Upsert FHIR Specimen rows into the ucla DB by resource id; returns (inserted, updated)."""
    from myhealth_fhir.db.engine import get_session_for
    from myhealth_fhir.db.ucla_unpack import extract_specimen
    from myhealth_fhir.models.ucla import Specimen

    inserted = 0
    updated = 0
    with get_session_for(provider) as session:
        for r in resources:
            rid = r.get("id", "")
            if not rid:
                continue
            type_obj = r.get("type", {})
            type_info = _coding_first_code(type_obj)
            note_text = ""
            for n in r.get("note", []):
                if isinstance(n, dict):
                    note_text = (note_text + "; " + n.get("text", "")) if note_text else n.get("text", "")
            sp_fields, _ = extract_specimen(r)
            existing = session.get(Specimen, rid)
            if existing:
                updated += 1
                existing.status = r.get("status")
                for k, v in sp_fields.items():
                    setattr(existing, k, v)
                existing.raw_json = json.dumps(r)
            else:
                session.add(Specimen(
                    fhir_id=rid, source="fhir", patient_id=_extract_patient_id(r),
                    encounter_id=_existing_encounter_id(session, r),
                    status=r.get("status"),
                    type_display=type_info.get("display", _cd(type_obj)),
                    type_system=type_info.get("system"), type_code=type_info.get("code"),
                    collected_datetime=_parse_dt(r.get("collection", {}).get("collectedDateTime") if isinstance(r.get("collection"), dict) else None),
                    received_datetime=_parse_dt(r.get("receivedTime")),
                    body_site=r.get("collection", {}).get("bodySite", {}).get("text") if isinstance(r.get("collection"), dict) else None,
                    note_text=note_text, raw_json=json.dumps(r),
                    **sp_fields,
                ))
                inserted += 1
        session.commit()
    return (inserted, updated)


# ── Communication (messages, phone encounters) ─────────────────────


def save_communications_to_db(resources: list[dict], provider: str = "ucla") -> tuple[int, int]:
    """Upsert FHIR Communication rows into the ucla DB by resource id; returns (inserted, updated)."""
    from myhealth_fhir.db.engine import get_session_for
    from myhealth_fhir.db.ucla_unpack import extract_communication
    from myhealth_fhir.models.ucla import Communication

    inserted = 0
    updated = 0
    with get_session_for(provider) as session:
        for r in resources:
            rid = r.get("id", "")
            if not rid:
                continue
            payload_parts = []
            for p in r.get("payload", []):
                if not isinstance(p, dict):
                    continue
                if p.get("contentString"):
                    payload_parts.append(str(p["contentString"]))
                else:
                    cc = p.get("contentCodeableConcept")
                    if isinstance(cc, dict):
                        payload_parts.append(_cd(cc) or cc.get("text", ""))
            note_text = ""
            for n in r.get("note", []):
                if isinstance(n, dict):
                    note_text = (note_text + "; " + n.get("text", "")) if note_text else n.get("text", "")
            sender_ref = _save_actor(session, r.get("sender"), provider, "Practitioner", rid)
            recipient = r.get("recipient", [{}])[0] if r.get("recipient") else None
            recipient_ref = _save_actor(session, recipient, provider, "Patient", rid) if isinstance(recipient, dict) else None
            cat = r.get("category", [{}])[0] if r.get("category") else {}
            co_fields, _ = extract_communication(r)
            existing = session.get(Communication, rid)
            if existing:
                updated += 1
                existing.status = r.get("status")
                existing.payload_text = "; ".join(p for p in payload_parts if p) or existing.payload_text
                for k, v in co_fields.items():
                    setattr(existing, k, v)
                existing.raw_json = json.dumps(r)
            else:
                session.add(Communication(
                    fhir_id=rid, source="fhir", patient_id=_extract_patient_id(r),
                    encounter_id=_existing_encounter_id(session, r),
                    status=r.get("status"),
                    category=_cd(cat) if isinstance(cat, dict) else None,
                    subject=r.get("subject", {}).get("display") if isinstance(r.get("subject"), dict) else None,
                    sent_datetime=_parse_dt(r.get("sent")), received_datetime=_parse_dt(r.get("received")),
                    sender_ref=sender_ref, recipient_ref=recipient_ref,
                    medium=r.get("medium", [{}])[0].get("coding", [{}])[0].get("display") if r.get("medium") else None,
                    payload_text="; ".join(p for p in payload_parts if p) or None,
                    note_text=note_text, raw_json=json.dumps(r),
                    **co_fields,
                ))
                inserted += 1
        session.commit()
    return (inserted, updated)


# ── CareTeam ───────────────────────────────────────────────────────


def save_care_teams_to_db(resources: list[dict], provider: str = "ucla") -> tuple[int, int]:
    """Upsert FHIR CareTeam rows into the ucla DB by resource id; returns (inserted, updated)."""
    from myhealth_fhir.db.engine import get_session_for
    from myhealth_fhir.db.ucla_unpack import extract_care_team
    from myhealth_fhir.models.ucla import CareTeam, CareTeamParticipant

    inserted = 0
    updated = 0
    with get_session_for(provider) as session:
        for r in resources:
            rid = r.get("id", "")
            if not rid:
                continue
            participants = []
            for p in r.get("participant", []):
                if not isinstance(p, dict):
                    continue
                member = p.get("member", {})
                participants.append(_cd(member) or member.get("display", "") if isinstance(member, dict) else "")
                role = p.get("role", [{}])[0] if p.get("role") else {}
                participants.append(f"[{_cd(role)}]" if isinstance(role, dict) and _cd(role) else "")
            note_text = ""
            for n in r.get("note", []):
                if isinstance(n, dict):
                    note_text = (note_text + "; " + n.get("text", "")) if note_text else n.get("text", "")
            cat = r.get("category", [{}])[0] if r.get("category") else {}
            ct_fields, ct_children = extract_care_team(r)
            existing = session.get(CareTeam, rid)
            if existing:
                updated += 1
                existing.status = r.get("status")
                existing.participants_display = " ".join(x for x in participants if x) or existing.participants_display
                for k, v in ct_fields.items():
                    setattr(existing, k, v)
                existing.raw_json = json.dumps(r)
            else:
                session.add(CareTeam(
                    fhir_id=rid, source="fhir", patient_id=_extract_patient_id(r),
                    encounter_id=_existing_encounter_id(session, r),
                    status=r.get("status"),
                    category=_cd(cat) if isinstance(cat, dict) else None,
                    name=r.get("name"),
                    period_start=_parse_dt(r.get("period", {}).get("start")),
                    period_end=_parse_dt(r.get("period", {}).get("end")),
                    participants_display=" ".join(x for x in participants if x) or None,
                    note_text=note_text, raw_json=json.dumps(r),
                    **ct_fields,
                ))
                inserted += 1
            session.query(CareTeamParticipant).filter(CareTeamParticipant.care_team_id == rid).delete(synchronize_session=False)
            for row in ct_children.get("care_team_participant", []):
                session.add(CareTeamParticipant(**row))
        session.commit()
    return (inserted, updated)


def save_document_references_to_db(resources: list[dict], provider: str = "ucla") -> tuple[int, int]:
    """Upsert FHIR DocumentReference rows into the ucla DB by resource id; returns (inserted, updated)."""
    from myhealth_fhir.db.engine import get_session_for
    from myhealth_fhir.db.ucla_unpack import extract_document_reference, upgrade_doc_displays
    from myhealth_fhir.models.ucla import DocumentReference, DocumentReferenceContent, DocumentReferenceIdentifier

    inserted = 0
    updated = 0
    with get_session_for(provider) as session:
        for r in resources:
            rid = r.get("id", "")
            if not rid:
                continue
            content = r.get("content", [{}])[0] if r.get("content") else {}
            attachment = content.get("attachment", {}) if isinstance(content, dict) else {}
            ctx = r.get("context", {})
            cat_list = []
            for c in r.get("category", []):
                if isinstance(c, dict):
                    cd = _cd(c)
                    if cd:
                        cat_list.append(cd)

            author_obj = r.get("author", [{}])[0] if r.get("author") and isinstance(r["author"][0], dict) else None
            author_ref = _save_actor(session, author_obj, provider, "Practitioner", rid)
            dr_fields, dr_children = extract_document_reference(r)
            upgrade_doc_displays(session, dr_fields, r, provider)
            existing = session.get(DocumentReference, rid)
            if existing:
                updated += 1
                existing.status = r.get("status")
                existing.description = r.get("description")
                existing.author_ref = author_ref
                for k, v in dr_fields.items():
                    setattr(existing, k, v)
                existing.raw_json = json.dumps(r)
            else:
                session.add(DocumentReference(
                    fhir_id=rid, patient_id=_extract_patient_id(r),
                    encounter_id=_existing_encounter_id(session, r),
                    status=r.get("status"),
                    type_display=r.get("type", {}).get("coding", [{}])[0].get("display") if isinstance(r.get("type"), dict) else None,
                    category=", ".join(cat_list) if cat_list else None,
                    date_created=_parse_dt(r.get("date")),
                    author_ref=author_ref,
                    description=r.get("description"),
                    content_url=attachment.get("url"), content_title=attachment.get("title"),
                    content_type=attachment.get("contentType"), size_bytes=attachment.get("size"),
                    facility=ctx.get("facilityType", {}).get("coding", [{}])[0].get("display") if isinstance(ctx, dict) and isinstance(ctx.get("facilityType"), dict) else None,
                    raw_json=json.dumps(r),
                    **dr_fields,
                ))
                inserted += 1
            session.query(DocumentReferenceContent).filter(DocumentReferenceContent.doc_id == rid).delete(synchronize_session=False)
            for row in dr_children.get("document_reference_content", []):
                session.add(DocumentReferenceContent(**row))
            session.query(DocumentReferenceIdentifier).filter(DocumentReferenceIdentifier.doc_id == rid).delete(synchronize_session=False)
            for row in dr_children.get("document_reference_identifier", []):
                session.add(DocumentReferenceIdentifier(**row))
        session.commit()
    return (inserted, updated)


# ── FamilyMemberHistory ────────────────────────────────────────────


def save_family_member_histories_to_db(resources: list[dict], provider: str = "ucla") -> tuple[int, int]:
    """Upsert FHIR FamilyMemberHistory rows into the ucla DB by resource id; returns (inserted, updated)."""
    from myhealth_fhir.db.engine import get_session_for
    from myhealth_fhir.db.ucla_unpack import extract_family_member_history
    from myhealth_fhir.models.ucla import FamilyMemberHistory

    inserted = 0
    updated = 0
    with get_session_for(provider) as session:
        for r in resources:
            rid = r.get("id", "")
            if not rid:
                continue
            conditions = []
            for c in r.get("condition", []):
                if isinstance(c, dict):
                    cc = c.get("code", {}).get("coding", [{}])[0] if isinstance(c.get("code"), dict) else {}
                    conditions.append(cc.get("display", c.get("code", {}).get("text", "")))
            note_text = ""
            for n in r.get("note", []):
                if isinstance(n, dict):
                    note_text = (note_text + "; " + n.get("text", "")) if note_text else n.get("text", "")

            family_member_ref = _save_actor(session, {"display": r.get("name")} if r.get("name") else None, provider, "FamilyMember", rid)
            fm_fields, _ = extract_family_member_history(r)
            existing = session.get(FamilyMemberHistory, rid)
            if existing:
                updated += 1
                existing.condition_display = "; ".join(c for c in conditions if c) if conditions else None
                existing.family_member_ref = family_member_ref
                for k, v in fm_fields.items():
                    setattr(existing, k, v)
                existing.raw_json = json.dumps(r)
            else:
                session.add(FamilyMemberHistory(
                    fhir_id=rid, patient_id=_extract_patient_id(r),
                    status=r.get("status"), relationship=r.get("relationship", {}).get("coding", [{}])[0].get("display") if isinstance(r.get("relationship"), dict) else None,
                    family_member_ref=family_member_ref, born_date=r.get("bornString"),
                    deceased_age=r.get("deceasedAge"),
                    condition_display="; ".join(c for c in conditions if c) if conditions else None,
                    condition_code=conditions[0] if conditions else None,
                    note_text=note_text, raw_json=json.dumps(r),
                    **fm_fields,
                ))
                inserted += 1
        session.commit()
    return (inserted, updated)


# ── ClinicalObservation (vitals, surveys, etc.) ────────────────────


def save_clinical_observations_to_db(resources: list[dict], provider: str = "ucla") -> tuple[int, int]:
    """Upsert FHIR Observation (vital signs / clinical) rows into the ucla DB; returns (inserted, updated)."""
    from myhealth_fhir.db.engine import get_session_for
    from myhealth_fhir.db.ucla_unpack import extract_clinical_observation
    from myhealth_fhir.models.ucla import ClinicalObservation, ClinicalObservationComponent

    inserted = 0
    updated = 0
    with get_session_for(provider) as session:
        for r in resources:
            rid = r.get("id", "")
            if not rid:
                continue
            code_obj = r.get("code", {})
            coding_list = code_obj.get("coding", []) if isinstance(code_obj, dict) else []
            loinc = _parse_loinc(coding_list)

            val_qty = r.get("valueQuantity")
            value_float, value_unit = None, None
            if isinstance(val_qty, dict):
                try:
                    value_float = float(val_qty["value"]) if val_qty.get("value") is not None else None
                except (TypeError, ValueError):
                    value_float = None
                value_unit = val_qty.get("unit", "")
            value_text = _extract_observation_value(r) or r.get("valueCodeableConcept", {}).get("text", "")

            ref_range_str = ""
            ref_ranges = r.get("referenceRange", [])
            if ref_ranges and isinstance(ref_ranges[0], dict):
                low = ref_ranges[0].get("low", {}).get("value")
                high = ref_ranges[0].get("high", {}).get("value")
                if low is not None and high is not None:
                    ref_range_str = f"{low} - {high}"
                elif low is not None:
                    ref_range_str = f">= {low}"

            interp_code, interp_display = None, None
            for i in r.get("interpretation", []):
                if isinstance(i, dict):
                    cc = i.get("coding", [])
                    if cc and isinstance(cc[0], dict):
                        interp_code = cc[0].get("code")
                        interp_display = cc[0].get("display")
                        break

            co_fields, co_children = extract_clinical_observation(r)
            component_value = co_fields.get("component_value")

            cat_list = r.get("category", [])
            cat_str = None
            if cat_list and isinstance(cat_list[0], dict):
                cat_str = cat_list[0].get("coding", [{}])[0].get("code")

            existing = session.get(ClinicalObservation, rid)
            if existing:
                updated += 1
                existing.value_text = str(value_float) + " " + value_unit if value_float is not None else value_text
                for k, v in co_fields.items():
                    if k == "component_value":
                        existing.component_value = component_value or existing.component_value
                    else:
                        setattr(existing, k, v)
                existing.raw_json = json.dumps(r)
            else:
                session.add(ClinicalObservation(
                    fhir_id=rid,
                    encounter_id=_existing_encounter_id(session, r),
                    category=cat_str,
                    code_display=_parse_code_display(code_obj),
                    code_text=code_obj.get("text") if isinstance(code_obj, dict) else None,
                    code_system=coding_list[0].get("system") if coding_list and isinstance(coding_list[0], dict) else None,
                    code_loinc=loinc,
                    value_text=str(value_float) + " " + value_unit if value_float is not None else (value_text or "(no value)"),
                    value_float=value_float, value_unit=value_unit,
                    reference_range=ref_range_str,
                    interpretation_code=interp_code, interpretation_display=interp_display,
                    effective_datetime=_parse_dt(r.get("effectiveDateTime")),
                    status=r.get("status"), raw_json=json.dumps(r),
                    source="fhir",
                    **co_fields,
                ))
                inserted += 1
            session.flush()
            obs = session.get(ClinicalObservation, rid)
            session.query(ClinicalObservationComponent).filter(ClinicalObservationComponent.observation_id == obs.id).delete(synchronize_session=False)
            for row in co_children.get("clinical_observation_component", []):
                session.add(ClinicalObservationComponent(observation_id=obs.id, **row))
        session.commit()
    return (inserted, updated)


# ── ClinicalNote (via DocumentReference → Binary) ──────────────

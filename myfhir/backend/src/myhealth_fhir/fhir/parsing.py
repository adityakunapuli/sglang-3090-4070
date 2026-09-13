"""Shared pure-FHIR parsing helpers.

Single home for the small FHIR-dict parsing helpers that were previously
duplicated across services/fhir_client.py, db/parser.py and db/ucla_unpack.py
(plan item P9). Pure functions only: no DB session, no network. The DB
touching helpers (_existing_encounter_id) import their models lazily inside
the function body. db/parser.py and db/ucla_unpack.py import from here so
each helper has exactly one implementation.
"""

import json
from datetime import UTC, date, datetime, time

from fhirpy import SyncFHIRClient

# ── Error / status helpers ────────────────────────────────────────


def _clean_error_message(err: Exception | str) -> str:
    """Format an error into a concise, human-readable description."""
    msg = str(err).strip()
    if not msg:
        return "Unknown error"
    try:
        data = json.loads(msg)
        if isinstance(data, dict):
            desc = data.get("error_description") or data.get("message") or data.get("error")
            code = data.get("status_code") or data.get("status")
            if desc:
                return f"{desc} (HTTP {code})" if code else str(desc)
    except Exception:
        pass
    return msg



def _fh_error_text(response) -> str:
    """Extract a short, log-safe message from a failed FHIR response."""
    try:
        body = response.json()
    except ValueError:
        body = {}
    if isinstance(body, dict):
        issue = body.get("issue")
        if isinstance(issue, list) and issue and isinstance(issue[0], dict):
            diag = issue[0].get("diagnostics")
            if diag:
                return str(diag)[:300]
        for key in ("error", "message", "detail"):
            if body.get(key):
                return str(body[key])[:300]
    text = (response.text or "").strip()
    return text[:120] if text else f"HTTP {response.status_code}"



def _max_last_updated(resources: list[dict] | None) -> datetime | None:
    """Newest meta.lastUpdated across a page of FHIR resources (None if absent)."""
    newest: datetime | None = None
    for rec in resources or []:
        if not isinstance(rec, dict):
            continue
        raw = (rec.get("meta") or {}).get("lastUpdated")
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        if newest is None or dt > newest:
            newest = dt
    return newest




# ── Generic FHIR field helpers ───────────────────────────────────


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



def _coding_first_code(code_obj: dict) -> dict:
    coding = code_obj.get("coding", []) if isinstance(code_obj, dict) else []
    for c in coding:
        if isinstance(c, dict) and c.get("code"):
            return {"system": c.get("system"), "code": c.get("code"), "display": c.get("display")}
    return {}




# ── Datetime parsing ─────────────────────────────────────────────


def parse_date(val):
    """Parse a FHIR date or dateTime string into a `date` (date part only)."""
    if not val:
        return None
    return date.fromisoformat(val.split("T")[0])



def _parse_dt(value):
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




# ── Identity / patient helpers ───────────────────────────────────


def client_ref(resource_type: str, resource_id: str, c: SyncFHIRClient):
    """Return a fhirpy Reference for resource_type/resource_id on client c."""
    return c.reference(resource_type, resource_id)



def _parse_loinc(code_list):
    """Extract LOINC code from a coding list."""
    if not isinstance(code_list, list):
        return None
    for c in code_list:
        if isinstance(c, dict) and c.get("system", "").endswith("loinc.org"):
            return c.get("code")
    return None



def _parse_code_display(code_obj):
    """Get display or text from a code object."""
    if not isinstance(code_obj, dict):
        return None
    coding = code_obj.get("coding", [])
    if coding and isinstance(coding[0], dict):
        return coding[0].get("display")
    return code_obj.get("text")



def _extract_patient_id(resource: dict) -> str | None:
    subj = resource.get("subject", resource.get("patient", {}))
    if isinstance(subj, dict):
        ref = subj.get("reference", "")
        if "/" in ref:
            return ref.split("/")[-1]
    return None



def _extract_encounter_id(resource: dict) -> str | None:
    enc = resource.get("encounter")
    if isinstance(enc, dict):
        ref = enc.get("reference", "")
        encounter_id = ref.split("/")[-1] if "/" in ref else ref
        return encounter_id or None
    return None



def _existing_encounter_id(session, resource: dict) -> str | None:
    """Keep only encounter references that exist in the local encounter table."""
    encounter_id = _extract_encounter_id(resource)
    if not encounter_id:
        return None
    from myhealth_fhir.models.ucla import Encounter

    return encounter_id if session.get(Encounter, encounter_id) is not None else None




# ── Observation value helpers ────────────────────────────────────


def _extract_observation_value(obs: dict) -> str | None:
    """Extract a human-readable value from any FHIR value[x] on an Observation-ish dict.

    Handles valueQuantity, valueString, valueBoolean, valueCodeableConcept, valueInteger,
    valueRange, valueRatio, valueTime, valueDateTime, and returns None when missing/absent.
    """
    val_qty = obs.get("valueQuantity")
    if isinstance(val_qty, dict):
        num = val_qty.get("value")
        unit = val_qty.get("unit", "") or val_qty.get("code", "")
        if num is not None:
            if isinstance(num, float) and num == int(num):
                return f"{int(num)} {unit}".strip()
            return f"{num} {unit}".strip()

    val_str = obs.get("valueString")
    if val_str:
        return str(val_str)

    val_bool = obs.get("valueBoolean")
    if val_bool is not None:
        return "Yes" if val_bool else "No"

    val_int = obs.get("valueInteger")
    if val_int is not None:
        return str(val_int)

    val_decimal = obs.get("valueDecimal")
    if val_decimal is not None:
        return str(val_decimal)

    val_time = obs.get("valueTime")
    if val_time:
        return str(val_time)

    val_dt = obs.get("valueDateTime")
    if val_dt:
        return str(val_dt)

    val_code = obs.get("valueCodeableConcept")
    if isinstance(val_code, dict):
        cc = val_code.get("coding", [])
        if cc:
            return cc[0].get("display", cc[0].get("code", ""))
        if val_code.get("text"):
            return val_code["text"]

    val_range = obs.get("valueRange")
    if isinstance(val_range, dict):
        low = val_range.get("low", {})
        high = val_range.get("high", {})
        lv = low.get("value") if isinstance(low, dict) else None
        hv = high.get("value") if isinstance(high, dict) else None
        lu = (low.get("unit", "") if isinstance(low, dict) else "") or ""
        if lv is not None and hv is not None:
            return f"{lv}-{hv} {lu}".strip()
        if lv is not None:
            return f">={lv} {lu}".strip()
        if hv is not None:
            return f"<={hv} {lu}".strip()

    val_ratio = obs.get("valueRatio")
    if isinstance(val_ratio, dict):
        nums = []
        for part in ("numerator", "denominator"):
            q = val_ratio.get(part, {})
            if isinstance(q, dict) and q.get("value") is not None:
                nums.append(str(q["value"]))
        if len(nums) == 2:
            return f"{nums[0]} : {nums[1]}"

    if obs.get("dataAbsentReason"):
        return "(absent)"
    return None




# ── Row builders ─────────────────────────────────────────────────


def _identifier_rows(resource: dict) -> list[dict]:
    """Flatten FHIR identifier[] into rows for a *_identifier child table."""
    rows = []
    for i, ident in enumerate(_as_list(resource.get("identifier"))):
        if not isinstance(ident, dict):
            continue
        rows.append({"seq": i, "system": ident.get("system"), "value": ident.get("value"), "use": ident.get("use")})
    return rows



def _extension_value(resource: dict, url: str):
    """Return the value* of the first top-level extension matching url (whitespace-insensitive)."""
    for ext in _as_list(resource.get("extension")):
        if isinstance(ext, dict) and "".join((ext.get("url") or "").split()) == url:
            for key in ("valueBoolean", "valueString", "valueInteger", "valueDecimal", "valueDate", "valueDateTime", "valueCode", "valueCoding"):
                if key in ext:
                    return ext[key]
    return None



def _component_rows(comp_list) -> tuple[str | None, list[dict]]:
    """Parse Observation.component[] into (component_value JSON, child-row dicts)."""
    import json

    component_value = None
    rows = []
    for i, comp in enumerate(_as_list(comp_list)):
        if not isinstance(comp, dict):
            continue
        c_code = comp.get("code", {})
        c_name = _parse_code_display(c_code) if isinstance(c_code, dict) else None
        c_val = _extract_observation_value(comp)
        cq = comp.get("valueQuantity")
        c_unit = cq.get("unit", cq.get("code")) if isinstance(cq, dict) else None
        c_float = None
        if isinstance(cq, dict) and cq.get("value") is not None:
            try:
                c_float = float(cq["value"])
            except (TypeError, ValueError):
                c_float = None
        rows.append(
            {
                "seq": i,
                "code_loinc": _parse_loinc(c_code.get("coding", []) if isinstance(c_code, dict) else []),
                "code_display": c_name,
                "component_value": c_val,
                "value_float": c_float,
                "value_unit": c_unit,
                "reference_range": None,
                "interpretation_code": None,
                "interpretation_display": None,
            }
        )
        for it in comp.get("interpretation", []) or []:
            if isinstance(it, dict) and it.get("coding") and isinstance(it["coding"][0], dict):
                rows[-1]["interpretation_code"] = it["coding"][0].get("code")
                rows[-1]["interpretation_display"] = it["coding"][0].get("display")
                break
    if rows:
        component_value = json.dumps([{"code": r["code_display"], "value": r["component_value"], "unit": r["value_unit"]} for r in rows])
    return component_value, rows


# ── Encounter ──────────────────────────────────────────────────────


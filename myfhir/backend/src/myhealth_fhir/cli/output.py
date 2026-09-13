"""CLI output formatting helpers.

Moved verbatim from cli.py (plan 5b). Pure FHIR-dict -> terminal-string
formatters; no command registration, no client construction.
"""

import json
import os
from collections import defaultdict
from typing import Any
from urllib.parse import parse_qs, urlparse

import click


def print_patient_eob_block(pid, eobs, date_label, all_pages, output, detailed):
    """Print a per-patient EOB block to stdout and optionally write JSONL output."""
    server_total = eobs.get("total", "unknown") if not all_pages else len(eobs)
    click.echo(click.style(f"\n--- Patient {pid} ---", bold=True))
    click.echo(f"Total EOB records: {len(eobs)} ({date_label})")

    if output:
        out_dir = os.path.dirname(os.path.abspath(output))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(output, "w", encoding="utf-8") as f:
            for resource in eobs:
                f.write(json.dumps(resource) + "\n")
        click.echo(click.style(f"[SUCCESS] Saved {len(eobs)} EOB records to {output}", fg="green", bold=True))

    if detailed:
        for resource in eobs:
            click.echo(click.style(f"\n--- EOB #{resource.get('id')} ---", bold=True, fg="green"))
            print_eob_detailed(resource)
    else:
        print_eob_metrics(eobs, server_total)



def extract_code(raw: str) -> str:
    """Extract the OAuth authorization code from a redirect URL or raw code string."""
    raw = raw.strip()
    parse_url = raw if "://" in raw else f"http://dummy_host?{raw.lstrip('?')}"
    parsed = urlparse(parse_url)
    qs = parse_qs(parsed.query)
    if "code" in qs and qs["code"]:
        return qs["code"][0].strip()
    return raw


def print_token_status(token) -> None:
    """Pretty-print the remaining lifetime and scope of a token."""
    remaining = token.seconds_remaining
    h, rem = divmod(int(remaining), 3600)
    m, s = divmod(rem, 60)
    click.echo(f"  Expires In:        {h}h {m}m {s}s")
    click.echo(f"  Expires At (UTC):  {token.expires_at.isoformat()}")
    if token.refresh_token:
        rexp = "EXPIRED" if token.refresh_expired else f"{token.refresh_token_expires_in // 3600:.0f}h remaining"
        fg = "red" if token.refresh_expired else "green"
        click.echo(click.style(f"  Refresh Token:   stored ({rexp})", fg=fg))
    else:
        click.echo(click.style("  Refresh Token:   None (re-auth required on expiry)", fg="yellow"))
    click.echo(f"  Scopes:            {token.scope}")


def eob_total_category(c: dict) -> str:
    """Return a human-readable label for an EOB total category entry."""
    cat = c.get("category")
    if isinstance(cat, dict):
        coding = cat.get("coding", [])
        if coding and isinstance(coding, list) and coding[0]:
            display = coding[0].get("display")
            if display:
                return display
        if cat.get("text"):
            return cat["text"]
    return "total"


def print_eob_summary(eob: dict) -> None:
    """Print a concise one-block summary of a single EOB resource."""
    click.echo(f"  Status: {eob.get('status', 'Unknown')}")
    click.echo(f"  Use:    {eob.get('use', 'Unknown')}")
    created = eob.get("created", "Unknown")
    created_str = created.get("value", "Unknown") if isinstance(created, dict) else created
    click.echo(f"  Created: {created_str}")
    patient = eob.get("patient", {})
    if isinstance(patient, dict):
        click.echo(f"  Patient: {patient.get('reference', '')}")
    for c in eob.get("total", []):
        cat = eob_total_category(c)
        amt = c.get("amount", {})
        val = amt.get("value") if isinstance(amt, dict) else None
        cur = amt.get("currency", "USD") if isinstance(amt, dict) else "USD"
        if val is not None and val != 0:
            click.echo(f"  {cat:20s}: {val:>10.2f} {cur}")
    payment = eob.get("payment", {})
    if isinstance(payment, dict):
        pamt = payment.get("amount", {})
        if isinstance(pamt, dict):
            click.echo(
                click.style(f"  {'Payment':20s}: {pamt.get('value', '?'):>10} {pamt.get('currency', '')}", bold=True)
            )


def print_eob_metrics(eobs: list, server_total) -> None:
    """Print aggregate metrics (counts, financial totals, date range) across many EOBs."""
    patients = defaultdict(list)
    total_payment = 0.0
    total_entered = 0.0
    total_benefit = 0.0
    total_paid_provider = 0.0
    total_paid_patient = 0.0
    date_range_start = None
    date_range_end = None
    status_counts = defaultdict(int)

    for eob in eobs:
        patient = eob.get("patient", {})
        patient_ref = patient.get("reference", "Unknown") if isinstance(patient, dict) else "Unknown"
        patients[patient_ref].append(eob)

        status_counts[eob.get("status", "unknown")] += 1

        created_str = eob.get("created", "")
        if created_str:
            if date_range_start is None or created_str < date_range_start:
                date_range_start = created_str
            if date_range_end is None or created_str > date_range_end:
                date_range_end = created_str

        for c in eob.get("total", []):
            amt = c.get("amount", {})
            if not isinstance(amt, dict) or amt.get("value") is None:
                continue
            val = amt["value"]
            eob_total_category(c)
            code = (
                c.get("category", {}).get("coding", [{}])[0].get("code", "")
                if isinstance(c.get("category"), dict)
                else ""
            )
            if code == "submitted":
                total_entered += val
            elif code == "benefit":
                total_benefit += val
            elif code == "paidtoprovider":
                total_paid_provider += val
            elif code == "paidbypatient":
                total_paid_patient += val

        payment = eob.get("payment", {})
        if isinstance(payment, dict):
            pamt = payment.get("amount", {})
            if isinstance(pamt, dict) and pamt.get("value"):
                total_payment += pamt["value"]

    click.echo(click.style(f"\n{'=' * 60}", bold=True))
    click.echo(click.style("  EOB Summary Metrics", bold=True, fg="cyan"))
    click.echo(click.style(f"{'=' * 60}", bold=True))

    click.echo(f"\n  Patients: {len(patients)}")
    for p, eobs_by_patient in patients.items():
        click.echo(f"    - {p}  ({len(eobs_by_patient)} EOBs)")

    click.echo(f"\n  Date Range: {date_range_start or 'N/A'} → {date_range_end or 'N/A'}")
    click.echo("  Status Breakdown:")
    for st, cnt in sorted(status_counts.items()):
        click.echo(f"    - {st}: {cnt}")

    click.echo("\n  Financial Totals:")
    click.echo(f"    Entered Amount:     ${total_entered:>12,.2f}")
    click.echo(f"    Benefit Amount:     ${total_benefit:>12,.2f}")
    click.echo(f"    Paid to Provider:   ${total_paid_provider:>12,.2f}")
    click.echo(f"    Paid by Patient:    ${total_paid_patient:>12,.2f}")
    click.echo(click.style(f"    Total Payment:      ${total_payment:>12,.2f}", bold=True, fg="green"))
    click.echo(click.style(f"{'=' * 60}", bold=True))


def print_eob_detailed(eob: dict) -> None:
    """Print a detailed, line-by-line view of a single EOB resource."""
    click.echo(f"  Status:          {eob.get('status', 'Unknown')}")
    click.echo(f"  Use:             {eob.get('use', 'Unknown')}")
    created = eob.get("created", "Unknown")
    created_str = created.get("value", "Unknown") if isinstance(created, dict) else created
    click.echo(f"  Created:         {created_str}")
    billable = eob.get("billablePeriod", {})
    if isinstance(billable, dict):
        click.echo(f"  Billable Period: {billable.get('start', 'N/A')} to {billable.get('end', 'N/A')}")
    patient = eob.get("patient", {})
    if isinstance(patient, dict):
        click.echo(f"  Patient:         {patient.get('reference', 'Unknown')}")
    provider = eob.get("provider", {})
    if isinstance(provider, dict):
        click.echo(f"  Billing Provider:{provider.get('reference', 'Unknown')}")
    facility = eob.get("facility", {})
    if isinstance(facility, dict) and facility.get("reference"):
        click.echo(f"  Facility:        {facility.get('reference', '')}")
    care_team = eob.get("careTeam", [])
    if care_team:
        click.echo("  Care Team:")
        for member in care_team:
            if isinstance(member, dict):
                ref = member.get("provider", {}).get("reference", "Unknown")
                role_coding = member.get("role", {}).get("coding", [{}])
                role = role_coding[0].get("display", "Provider") if role_coding else "Provider"
                click.echo(f"    - {role}: {ref}")
    diagnoses = eob.get("diagnosis", [])
    if diagnoses:
        click.echo("  Diagnoses (ICD):")
        for diag in diagnoses:
            if isinstance(diag, dict):
                seq = diag.get("sequence", "?")
                codeable = diag.get("diagnosisCodeableConcept", {})
                coding = codeable.get("coding", [{}]) if isinstance(codeable, dict) else [{}]
                code = coding[0].get("code", "Unknown") if coding else "Unknown"
                desc = coding[0].get("display", "") if coding else ""
                click.echo(f"    - [{seq}] {code} : {desc}")
    items = eob.get("item", [])
    if items:
        click.echo("  Claim Line Items:")
        for item in items:
            if isinstance(item, dict):
                seq = item.get("sequence", "?")
                prod = item.get("productOrService", {})
                coding = prod.get("coding", [{}]) if isinstance(prod, dict) else [{}]
                code = coding[0].get("code", "Unknown") if coding else "Unknown"
                desc = coding[0].get("display", "") if coding else ""
                date = item.get("servicedDate") or item.get("servicedPeriod", {}).get("start", "Unknown")
                click.echo(f"    Item #{seq} | Date: {date} | Code: {code} ({desc})")
                loc_coding = (
                    item.get("locationCodeableConcept", {}).get("coding", [{}])
                    if isinstance(item.get("locationCodeableConcept"), dict)
                    else [{}]
                )
                loc = loc_coding[0].get("display", "Unknown") if loc_coding else "Unknown"
                click.echo(f"      Location: {loc}")
                adjudications = item.get("adjudication", [])
                adj_lines = []
                for adj in adjudications:
                    if isinstance(adj, dict):
                        cat_coding = (
                            adj.get("category", {}).get("coding", [{}])
                            if isinstance(adj.get("category"), dict)
                            else [{}]
                        )
                        cat = cat_coding[0].get("code", "") if cat_coding else ""
                        amt = adj.get("amount", {})
                        if cat and isinstance(amt, dict) and amt.get("value") is not None:
                            adj_lines.append(f"{cat}: {amt.get('value')} {amt.get('currency', 'USD')}")
                if adj_lines:
                    click.echo(f"      Financials: {', '.join(adj_lines)}")
    payment = eob.get("payment", {})
    if isinstance(payment, dict):
        pamt = payment.get("amount", {})
        if isinstance(pamt, dict):
            click.echo(
                click.style(
                    f"  Payment amount:  {pamt.get('value', '?')} {pamt.get('currency', 'USD')}", bold=True, fg="cyan"
                )
            )


def print_coverage_summary(cov: dict) -> None:
    """Print a concise summary of a Coverage resource."""
    click.echo(f"  Status:  {cov.get('status', 'Unknown')}")
    payor = cov.get("payor", [{}])
    if payor and isinstance(payor[0], dict):
        click.echo(f"  Payor:   {payor[0].get('display', 'Unknown')}")
    period = cov.get("period", {})
    click.echo(f"  Period:  {period.get('start', 'N/A')} to {period.get('end', 'N/A')}")
    ben = cov.get("beneficiary", {})
    if isinstance(ben, dict):
        click.echo(f"  Beneficiary: {ben.get('reference', 'Unknown')}")


def print_claim_summary(claim: dict) -> None:
    """Print a concise summary of a Claim resource."""
    click.echo(f"  Status: {claim.get('status', 'Unknown')}")
    click.echo(f"  Use:    {claim.get('use', 'Unknown')}")
    enterer = claim.get("enterer", {})
    if isinstance(enterer, dict):
        click.echo(f"  Enterer: {enterer.get('display', enterer.get('reference', 'Unknown'))}")
    patient = claim.get("patient", {})
    if isinstance(patient, dict):
        click.echo(f"  Patient: {patient.get('reference', '')}")
    totals = []
    claim_total = claim.get("total", [])
    if isinstance(claim_total, dict):
        claim_total = [claim_total]
    elif not isinstance(claim_total, list):
        claim_total = []
    for c in claim_total[:5]:
        if not isinstance(c, dict):
            continue
        amt = c.get("amount", {})
        if isinstance(amt, dict):
            totals.append(f"  Total: {amt.get('value', '?')} {amt.get('currency', '')}")
    for t in totals:
        click.echo(t)


def print_organization_summary(org: dict) -> None:
    """Print a concise summary of an Organization resource."""
    click.echo(f"  Name:    {org.get('name', 'Unknown')}")
    click.echo(f"  Active:  {org.get('active', 'Unknown')}")
    for contact in org.get("contact", []):
        purpose = contact.get("purpose", {})
        if isinstance(purpose, dict):
            role = purpose.get("text", purpose.get("coding", [{}])[0].get("display", "contact"))
        else:
            role = str(purpose) if purpose else "contact"
        cname = contact.get("name", {})
        if isinstance(cname, dict):
            cstr = f"{cname.get('family', '')}, {' '.join(cname.get('given', []))}" or "Unknown"
        else:
            cstr = str(cname) if cname else "Unknown"
        click.echo(f"  Contact ({role}): {cstr}")


def print_observation_summary(obs: dict) -> None:
    """Print a concise summary of an Observation (lab result) resource."""
    click.echo(click.style(f"\n--- Observation #{obs.get('id')} ---", bold=True, fg="blue"))
    code = obs.get("code", {})
    coding = code.get("coding", [{}]) if isinstance(code, dict) else [{}]
    code_str = coding[0].get("display", coding[0].get("code", "Unknown")) if coding else "Unknown"
    click.echo(f"  Test:  {code_str}")
    val = obs.get("value", {})
    if isinstance(val, dict):
        if isinstance(val, str):
            click.echo(f"  Value: {val}")
        else:
            unit = val.get("unit", "")
            val.get("system", "")
            if val.get("value") is not None:
                click.echo(f"  Value: {val['value']} {unit}")
            else:
                click.echo("  Value: (complex result)")
    else:
        click.echo(f"  Value: {val}")
    status = obs.get("status", "Unknown")
    click.echo(f"  Status: {status}")
    effective = obs.get("effective", "Unknown")
    if isinstance(effective, str):
        click.echo(f"  Date:   {effective}")
    elif isinstance(effective, dict):
        click.echo(f"  Date:   {effective.get('value', 'N/A')}")


def _get_observation_value_str(obs: dict) -> str:
    """Extract a human-readable value string from an Observation."""
    # String value
    if obs.get("valueString"):
        return obs["valueString"]
    # Boolean
    if "valueBoolean" in obs:
        return "Yes" if obs["valueBoolean"] else "No"
    # Date
    if obs.get("valueDate"):
        return obs["valueDate"]
    # Integer
    if obs.get("valueInteger") is not None:
        return str(obs["valueInteger"])
    # Quantity (numeric with units)
    val = obs.get("valueQuantity")
    if isinstance(val, dict):
        num = val.get("value")
        unit = val.get("unit", "")
        if num is not None:
            if isinstance(num, float) and num == int(num):
                return f"{int(num)} {unit}".strip()
            return f"{num} {unit}".strip()
    # Code (categorical result like "Positive", "1+")
    val_code = obs.get("valueCodeableConcept")
    if isinstance(val_code, dict):
        coding = val_code.get("coding", [])
        if coding:
            display = coding[0].get("display", coding[0].get("code", ""))
            if display:
                return str(display)
        text = val_code.get("text")
        if text:
            return str(text)
    # Data absent
    if obs.get("dataAbsentReason"):
        dar = obs["dataAbsentReason"]
        if isinstance(dar, dict):
            coding = dar.get("coding", [])
            if coding:
                return coding[0].get("display", coding[0].get("code", "absent"))
            return dar.get("text", "absent")
        return "absent"
    return "(no value)"


def _get_reference_range(obs: dict) -> str:
    """Extract reference range string from an Observation.

    Handles both structured (low/high Quantity) and plain text ranges (Epic).
    """
    ranges = obs.get("referenceRange", [])
    if not ranges:
        return ""
    parts = []
    for r in ranges:
        type_display = ""
        # Plain text range (Epic style: "65 - 99")
        text = r.get("text")
        if text:
            parts.append(text)
            continue

        # Structured low/high
        low = r.get("low", {})
        high = r.get("high", {})
        low_val = low.get("value") if isinstance(low, dict) else None
        high_val = high.get("value") if isinstance(high, dict) else None
        low_unit = low.get("unit", "") if isinstance(low, dict) else ""
        type_ = r.get("type", {})
        if isinstance(type_, dict):
            coding = type_.get("coding", [])
            if coding:
                type_display = f" [{coding[0].get('display', '')}]"

        if low_val is not None and high_val is not None:
            parts.append(f"{low_val}-{high_val} {low_unit}".strip())
        elif low_val is not None:
            parts.append(f">={low_val} {low_unit}".strip())
        elif high_val is not None:
            high_unit = high.get("unit", "") if isinstance(high, dict) else ""
            parts.append(f"<={high_val} {high_unit}".strip())
    return ", ".join(parts) + type_display if parts else ""


def _get_abnormal_flag(obs: dict) -> str:
    """Get the abnormal flag with color coding."""
    flag = obs.get("interpretation", [])
    if not flag:
        return ""
    if isinstance(flag, list):
        codes = []
        for f in flag:
            if isinstance(f, dict):
                coding = f.get("coding", [])
                if coding:
                    codes.append(coding[0].get("code", ""))
        flag_str = ", ".join(codes)
    else:
        flag_str = str(flag)

    # Map to display
    flag_map = {
        "H": ("High", "red"),
        "L": ("Low", "yellow"),
        "HH": ("Critical H", "red"),
        "LL": ("Critical L", "red"),
        "N": ("Normal", "green"),
        "C": ("Critical", "red"),
        "A": ("Abnormal", "yellow"),
        "AA": ("Critical", "red"),
        "HU": ("High+", "red"),
        "LU": ("Low+", "red"),
        ">": ("High", "red"),
        "<": ("Low", "yellow"),
    }
    result_parts = []
    for code in flag_str.split(","):
        code = code.strip()
        if code in flag_map:
            label, color = flag_map[code]
            result_parts.append(click.style(label, fg=color, bold=True))
        elif code:
            result_parts.append(code)
    return " ".join(result_parts) if result_parts else ""


def print_lab_panel(panel: dict, include_observations: bool = True, detailed: bool = False, child_observations: list | None = None, fetch_client: Any = None):
    """Print a DiagnosticReport lab panel with child observations.

    If child_observations is not provided and include_observations is True,
    fetches child Observations on-demand from the panel's result references.
    """
    code = panel.get("code", {})
    coding = code.get("coding", [{}]) if isinstance(code, dict) else [{}]
    panel_name = coding[0].get("display", coding[0].get("code", "Lab Panel")) if coding else "Lab Panel"
    # Fallback to text field (Epic often uses this)
    if not panel_name or panel_name == "Lab Panel":
        panel_name = code.get("text", panel_name) if isinstance(code, dict) else "Lab Panel"

    status = panel.get("status", "unknown")
    status_colors = {"final": "green", "preliminary": "yellow", "partial": "yellow", "amended": "blue", "corrected": "cyan", "cancelled": "red"}
    status_display = click.style(status, fg=status_colors.get(status, "white"))

    # Handle both effectiveDateTime (Epic) and effective dict
    effective = panel.get("effectiveDateTime", panel.get("effective", "N/A"))
    if isinstance(effective, dict):
        effective = effective.get("start", effective.get("end", effective.get("value", "N/A")))
    # Format datetime nicely
    if isinstance(effective, str) and effective != "N/A":
        # Strip time for cleaner display, keep date
        effective = effective.replace("T", " ").split(".")[0]
        # Remove trailing Z
        effective = effective.rstrip("Z")

    # Handle subject (Epic) vs patient
    patient = panel.get("subject", panel.get("patient", {}))
    patient_ref = ""
    if isinstance(patient, dict):
        patient_ref = patient.get("display", patient.get("reference", ""))

    performer = panel.get("performer", [])
    performer_str = ""
    if performer:
        performers = []
        for p in performer:
            if isinstance(p, dict):
                d = p.get("display", p.get("reference", ""))
                if d:
                    performers.append(d)
        performer_str = ", ".join(performers[:2])  # Show first 2 performers

    click.echo(click.style(f"\n{'─' * 60}", fg="cyan"))
    click.echo(click.style(f"  🧪 {panel_name}", bold=True, fg="cyan"))
    click.echo(f"  Status: {status_display}  |  Date: {effective}  |  Patient: {patient_ref}")
    if performer_str:
        click.echo(f"  Lab: {performer_str}")

    # Conclusion / interpretation
    conclusion = panel.get("conclusion")
    if conclusion:
        click.echo(click.style(f"  Notes: {conclusion}", fg="yellow"))

    # Child observations
    obs_list = child_observations or []

    # If no child observations provided but we want them, fetch on-demand
    if not obs_list and include_observations:
        result_refs = panel.get("result", [])
        if result_refs and fetch_client:
            # Filter out narrative-only observations
            obs_list = []
            for ref in result_refs:
                ref_str = ref.get("reference", "") if isinstance(ref, dict) else str(ref)
                display = ref.get("display", "") if isinstance(ref, dict) else ""
                obs_id = ref_str.split("/")[-1] if "/" in ref_str else ref_str
                # Skip "Narrative" entries — they're just summaries
                if display and "Narrative" in display:
                    continue
                try:
                    obs = fetch_client.get("Observation", obs_id)
                    obs_list.append(obs)
                except Exception:
                    pass
            result_refs = []  # Mark as fetched

    if obs_list:
        click.echo(f"\n  {'Test':<30} {'Result':<18} {'Ref Range':<22} Flag")
        click.echo(f"  {'─' * 30} {'─' * 18} {'─' * 22}")
        for obs in obs_list:
            obs_code = obs.get("code", {})
            obs_coding = obs_code.get("coding", [{}]) if isinstance(obs_code, dict) else [{}]
            test_name = obs_coding[0].get("display", obs_coding[0].get("code", "Unknown")) if obs_coding else obs_code.get("text", "Unknown")
            if not test_name or test_name == "Unknown":
                test_name = obs_code.get("text", "Unknown") if isinstance(obs_code, dict) else "Unknown"

            value_str = _get_observation_value_str(obs)
            ref_range = _get_reference_range(obs)
            flag = _get_abnormal_flag(obs)

            # Truncate
            if len(test_name) > 29:
                test_name = test_name[:26] + "..."
            if len(value_str) > 17:
                value_str = value_str[:14] + "..."
            if len(ref_range) > 21:
                ref_range = ref_range[:18] + "..."

            flag_str = flag if flag else ""
            base_line = f"  {test_name:<30} {value_str:<18} {ref_range:<22}"
            click.echo(base_line + flag_str)

            if detailed:
                eff_obs = obs.get("effectiveDateTime", obs.get("effective", ""))
                if eff_obs and eff_obs != "N/A":
                    click.echo(f"    → Date: {eff_obs}")
                if obs.get("status") and obs["status"] != "final":
                    click.echo(f"    → Status: {obs['status']}")

    else:
        # Show result references if not fetched
        results = panel.get("result", [])
        narrative_refs = [r for r in results if isinstance(r, dict) and "Narrative" in r.get("display", "")]
        real_refs = [r for r in results if r not in narrative_refs]
        if real_refs:
            click.echo(f"  Results: {len(real_refs)} test(s) available (--panel-id {panel.get('id')} to view)")
        elif results:
            click.echo(f"  Results: {len(results)} (narrative summary only)")
        else:
            click.echo("  No results included in this panel.")


def print_medication_summary(ms: dict) -> None:
    """Print a concise summary of a MedicationStatement resource."""
    click.echo(click.style(f"\n--- Medication #{ms.get('id')} ---", bold=True, fg="magenta"))
    med = ms.get("medication", {})
    if isinstance(med, dict):
        display = med.get("display", med.get("reference", "Unknown"))
        click.echo(f"  Medication: {display}")
    status = ms.get("status", "Unknown")
    click.echo(f"  Status:     {status}")
    category = ms.get("category", {})
    if isinstance(category, dict):
        click.echo(f"  Category:   {category.get('display', 'N/A')}")
    effective = ms.get("effectivePeriod", {})
    if isinstance(effective, dict):
        click.echo(f"  Period:     {effective.get('start', 'N/A')} to {effective.get('end', 'N/A')}")


def print_encounter_summary(enc: dict) -> None:
    """Print a concise summary of an Encounter resource."""
    click.echo(click.style(f"\n--- Encounter #{enc.get('id')} ---", bold=True, fg="cyan"))
    status = enc.get("status", "Unknown")
    click.echo(f"  Status:   {status}")
    class_ = enc.get("class", "Unknown")
    click.echo(f"  Class:    {class_}")
    type_ = enc.get("type", [])
    if type_ and isinstance(type_, list):
        for t in type_[:1]:
            if isinstance(t, dict):
                click.echo(f"  Type:     {t.get('display', t.get('text', 'N/A'))}")
    date = enc.get("period", {}).get("start", "N/A") if isinstance(enc.get("period"), dict) else "N/A"
    click.echo(f"  Date:     {date}")
    provider = enc.get("serviceProvider", {})
    if isinstance(provider, dict):
        click.echo(f"  Provider: {provider.get('reference', 'N/A')}")


def print_condition_summary(cond: dict) -> None:
    """Print a concise summary of a Condition resource."""
    click.echo(click.style(f"\n--- Condition #{cond.get('id')} ---", bold=True, fg="yellow"))
    status = cond.get("clinicalStatus", {})
    if isinstance(status, dict):
        click.echo(f"  Status:   {status.get('display', status.get('coding', [{}])[0].get('code', 'Unknown'))}")
    code = cond.get("code", {})
    if isinstance(code, dict):
        coding = code.get("coding", [{}])
        click.echo(f"  Code:     {coding[0].get('display', coding[0].get('code', 'Unknown')) if coding else 'N/A'}")
    onset = cond.get("onsetDateTime", cond.get("onsetString", "N/A"))
    click.echo(f"  Onset:    {onset}")


def print_allergy_summary(allergy: dict) -> None:
    """Print a concise summary of an AllergyIntolerance resource."""
    click.echo(click.style(f"\n--- Allergy #{allergy.get('id')} ---", bold=True, fg="red"))
    status = allergy.get("clinicalStatus", {})
    if isinstance(status, dict):
        click.echo(f"  Status:   {status.get('display', 'Unknown')}")
    substance = allergy.get("code", {})
    if isinstance(substance, dict):
        coding = substance.get("coding", [{}])
        click.echo(
            f"  Substance: {coding[0].get('display', coding[0].get('code', 'Unknown')) if coding else 'Unknown'}"
        )
    severity = allergy.get("severity", "N/A")
    click.echo(f"  Severity: {severity}")
    type_ = allergy.get("type", "N/A")
    click.echo(f"  Type:     {type_}")


def print_immunization_summary(imm: dict) -> None:
    """Print a concise summary of an Immunization resource."""
    click.echo(click.style(f"\n--- Immunization #{imm.get('id')} ---", bold=True, fg="green"))
    vaccine = imm.get("vaccineCode", {})
    if isinstance(vaccine, dict):
        coding = vaccine.get("coding", [{}])
        click.echo(f"  Vaccine:  {coding[0].get('display', coding[0].get('code', 'Unknown')) if coding else 'Unknown'}")
    date = imm.get("date", "N/A")
    click.echo(f"  Date:     {date}")
    status = imm.get("status", "N/A")
    click.echo(f"  Status:   {status}")


def print_careplan_summary(cp: dict) -> None:
    """Print a concise summary of a CarePlan resource."""
    click.echo(click.style(f"\n--- CarePlan #{cp.get('id')} ---", bold=True, fg="cyan"))
    status = cp.get("status", "Unknown")
    click.echo(f"  Status:   {status}")
    title = cp.get("title", "N/A")
    click.echo(f"  Title:    {title}")
    period = cp.get("period", {})
    if isinstance(period, dict):
        click.echo(f"  Period:   {period.get('start', 'N/A')} to {period.get('end', 'N/A')}")


# ── Standalone `anthem` binary (no double "anthem anthem") ────


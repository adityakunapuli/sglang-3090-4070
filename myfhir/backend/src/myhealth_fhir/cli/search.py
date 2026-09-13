"""Global unauthenticated search commands (search-claims, search-eob)."""


import click

from myhealth_fhir.cli.main import main


@main.command(name="search-claims")
@click.option("--patient", "-p", help="Filter by patient ID (substring match)")
@click.option("--code", "-c", help="Filter by HCPCS code (substring match)")
@click.option("--icd", help="Filter by ICD code (substring match)")
@click.option("--diagnosis", help="Filter by diagnosis text (substring match)")
@click.option("--provider", help="Filter by care team provider (substring match)")
@click.option("--date-from", help="Earliest date (YYYY-MM-DD)")
@click.option("--date-to", help="Latest date (YYYY-MM-DD)")
@click.option("--items", is_flag=True, help="Item-level output (default output is claim-grain, deduped)")
@click.option("--limit", "-l", default=50, type=int, help="Max results")
@click.option("--raw", is_flag=True, help="Tab-separated output for CSV export")
def search_claims(patient, code, icd, diagnosis, provider, date_from, date_to, items, limit, raw):
    """Search stored claim submissions (vw_claims view, line-item grain)."""
    from myhealth_fhir.db import get_anthem_session

    view = "vw_claims"
    conditions = []
    params: dict = {}

    if patient:
        conditions.append("patient_id LIKE :patient")
        params["patient"] = f"%{patient}%"
    if icd:
        conditions.append("icd_codes LIKE :icd")
        params["icd"] = f"%{icd}%"
    if diagnosis:
        conditions.append("icd_displays LIKE :diagnosis")
        params["diagnosis"] = f"%{diagnosis}%"
    if code and items:
        conditions.append("hcpcs_code LIKE :code")
        params["code"] = f"%{code}%"
    if code and not items:
        conditions.append("claim_number IN (SELECT DISTINCT claim_number FROM vw_claims WHERE hcpcs_code LIKE :code)")
        params["code"] = f"%{code}%"
    if provider:
        conditions.append("care_team_providers LIKE :prov")
        params["prov"] = f"%{provider}%"
    if date_from:
        col = "created_date" if not items else "serviced_date"
        conditions.append(f"({col} >= :date_from OR billable_period_start >= :date_from)")
        params["date_from"] = date_from
    if date_to:
        col = "created_date" if not items else "serviced_date"
        conditions.append(f"({col} <= :date_to OR billable_period_end <= :date_to)")
        params["date_to"] = date_to

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    sql = f"SELECT * FROM {view} {where} LIMIT :limit"
    params["limit"] = limit

    from sqlalchemy import text

    with get_anthem_session() as session:
        rows = session.execute(text(sql), params).fetchall()
        cols = list(rows[0]._fields) if rows else []
        click.echo(click.style(f"\nResults: {len(rows)} row(s) from {view}", bold=True, fg="green"))
        if raw:
            if rows:
                click.echo("\t".join(cols))
                for r in rows:
                    click.echo("\t".join(str(getattr(r, c) or "") for c in cols))
        elif items:
            for r in rows:
                pname = f" ({r.patient_name})" if r.patient_name else ""
                claim_label = r.claim_number or r.claim_adjustment_key or "?"
                click.echo(click.style(f"\n── Claim {claim_label} item #{r.item_seq} ──", bold=True))
                click.echo(f"  Patient: {r.patient_id}{pname}")
                if r.hcpcs_code:
                    click.echo(f"  HCPCS: {r.hcpcs_code} — {r.hcpcs_display or ''}")
                click.echo(
                    f"  Service: {r.serviced_date or r.serviced_period_start} | "
                    f"Qty: {r.quantity} | Amt: {r.net_amount or r.unit_price}"
                )
        else:
            seen = set()
            for r in rows:
                if r.claim_number in seen:
                    continue
                seen.add(r.claim_number)
                pname = f" ({r.patient_name})" if r.patient_name else ""
                claim_label = r.claim_number or r.claim_adjustment_key or "?"
                click.echo(click.style(f"\n── Claim {claim_label} ──", bold=True))
                click.echo(f"  Patient: {r.patient_id}{pname} | Status: {r.status}")
                if r.provider_name and r.provider_name != r.patient_id:
                    click.echo(f"  Provider: {r.provider_name}")
                if r.icd_codes:
                    click.echo(f"  ICD: {r.icd_codes}")
                if r.icd_displays:
                    click.echo(f"  Diagnosis: {r.icd_displays[:120]}")
                if r.care_team_providers:
                    click.echo(f"  Care Team: {r.care_team_providers}")
                if r.total_amount:
                    click.echo(f"  Total: {r.total_amount} USD")
                if r.eob_status:
                    paid = f" | Paid: {r.eob_payment_amount}" if r.eob_payment_amount else ""
                    click.echo(f"  Linked EOB: {r.eob_status} ({r.eob_disposition or '-'}){paid}")
        if not rows:
            click.echo("No results.")


@main.command(name="search-eob")
@click.option("--patient", "-p", help="Filter by patient ID (substring match)")
@click.option("--hcpcs", help="Filter by HCPCS code (substring match, item-level)")
@click.option("--icd", help="Filter by ICD code (substring match)")
@click.option("--diagnosis", help="Filter by diagnosis text (substring match)")
@click.option("--provider", help="Filter by care team provider (substring match)")
@click.option("--date-from", help="Earliest service or billable date (YYYY-MM-DD)")
@click.option("--date-to", help="Latest service or billable date (YYYY-MM-DD)")
@click.option("--amt-min", type=float, help="Minimum net/submitted amount")
@click.option("--amt-max", type=float, help="Maximum net/submitted amount")
@click.option("--status", help="EOB status (active, historical, etc.)")
@click.option("--claims", is_flag=True, help="Claim-grain output (deduped) instead of one row per line item")
@click.option("--limit", "-l", default=50, type=int, help="Max results")
@click.option("--raw", is_flag=True, help="Tab-separated output for CSV export")
def search_eob(
    patient, hcpcs, icd, diagnosis, provider, date_from, date_to, amt_min, amt_max, status, claims, limit, raw
):
    """Search denormalized EOB data (vw_eob view, line-item grain)."""
    from myhealth_fhir.db import get_anthem_session

    view = "vw_eob"
    conditions = []
    params: dict = {}

    if patient:
        conditions.append("patient_id LIKE :patient")
        params["patient"] = f"%{patient}%"
    if icd and not claims:
        conditions.append("claim_number IN (SELECT DISTINCT claim_number FROM vw_eob WHERE icd_codes LIKE :icd)")
        params["icd"] = f"%{icd}%"
    if icd and claims:
        conditions.append("icd_codes LIKE :icd")
        params["icd"] = f"%{icd}%"
    if diagnosis and not claims:
        conditions.append("claim_number IN (SELECT DISTINCT claim_number FROM vw_eob WHERE icd_displays LIKE :diagnosis)")
        params["diagnosis"] = f"%{diagnosis}%"
    if diagnosis and claims:
        conditions.append("icd_displays LIKE :diagnosis")
        params["diagnosis"] = f"%{diagnosis}%"
    if hcpcs and not claims:
        conditions.append("hcpcs_code LIKE :hcpcs")
        params["hcpcs"] = f"%{hcpcs}%"
    if hcpcs and claims:
        conditions.append("claim_number IN (SELECT DISTINCT claim_number FROM vw_eob WHERE hcpcs_code LIKE :hcpcs)")
        params["hcpcs"] = f"%{hcpcs}%"
    if provider:
        conditions.append("care_team_providers LIKE :prov")
        params["prov"] = f"%{provider}%"
    if date_from:
        if claims:
            conditions.append("created_date >= :date_from")
        else:
            conditions.append("(serviced_date >= :date_from OR serviced_period_start >= :date_from OR created_date >= :date_from)")
        params["date_from"] = date_from
    if date_to:
        if claims:
            conditions.append("created_date <= :date_to")
        else:
            conditions.append("(serviced_date <= :date_to OR serviced_period_end <= :date_to OR created_date <= :date_to)")
        params["date_to"] = date_to
    if amt_min is not None:
        col = "net_amount" if not claims else "total_submitted"
        conditions.append(f"({col} >= :amt_min OR submitted_amount >= :amt_min)")
        params["amt_min"] = amt_min
    if amt_max is not None:
        col = "net_amount" if not claims else "total_submitted"
        conditions.append(f"({col} <= :amt_max OR submitted_amount <= :amt_max)")
        params["amt_max"] = amt_max
    if status:
        conditions.append("status = :status")
        params["status"] = status

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    sql = f"SELECT * FROM {view} {where} LIMIT :limit"
    params["limit"] = limit

    from sqlalchemy import text

    with get_anthem_session() as session:
        rows = session.execute(text(sql), params).fetchall()
        cols = list(rows[0]._fields) if rows else []
        click.echo(click.style(f"\nResults: {len(rows)} row(s) from {view}", bold=True, fg="green"))
        if raw:
            if rows:
                click.echo("\t".join(cols))
                for r in rows:
                    click.echo("\t".join(str(getattr(r, c) or "") for c in cols))
        elif claims:
            seen = set()
            for r in rows:
                if r.claim_number in seen:
                    continue
                seen.add(r.claim_number)
                pname = f" ({r.patient_name})" if r.patient_name else ""
                click.echo(click.style(f"\n── EOB {r.claim_number or '?'} ──", bold=True))
                click.echo(f"  Patient: {r.patient_id}{pname} | Status: {r.status}")
                if r.provider_name and r.provider_name != r.patient_id:
                    click.echo(f"  Provider: {r.provider_name}")
                if r.payee_name and r.payee_name != r.patient_id:
                    click.echo(f"  Payee: {r.payee_name}")
                click.echo(f"  Dates: created {r.created_date} → paid {r.payment_date}")
                if r.icd_codes:
                    click.echo(f"  ICD: {r.icd_codes}")
                if r.icd_displays:
                    click.echo(f"  Diagnosis: {r.icd_displays[:180]}")
                if r.care_team_providers:
                    click.echo(f"  Providers: {r.care_team_providers}")
                if r.total_submitted or r.total_benefit:
                    click.echo(
                        f"  Submitted: {r.total_submitted} | Benefit: {r.total_benefit} | "
                        f"Deductible: {r.total_deductible}"
                    )
                if r.claim_status:
                    click.echo(f"  Linked Claim: {r.claim_status} | Submitted: {r.claim_total_amount}")
        else:
            for r in rows:
                pname = f" ({r.patient_name})" if r.patient_name else ""
                click.echo(click.style(f"\n── EOB {r.claim_number or '?'} item #{r.item_seq} ──", bold=True))
                click.echo(f"  Patient: {r.patient_id}{pname} | Status: {r.status}")
                if r.hcpcs_code:
                    click.echo(f"  HCPCS: {r.hcpcs_code} — {r.hcpcs_display or ''}")
                click.echo(f"  Service: {r.serviced_date or r.serviced_period_start} | Loc: {r.location_code or ''}")
                if r.net_amount or r.submitted_amount:
                    click.echo(f"  Amount: submitted={r.submitted_amount} net={r.net_amount} paid={r.paid_provider}")
        if not rows:
            click.echo("No results.")

"""Anthem-specific resource commands (eob, patients, coverage, claims, update, submission)."""

from datetime import date
from typing import Any

import click
from tqdm import tqdm

from myhealth_fhir.cli.auth import do_auth, do_login
from myhealth_fhir.cli.main import anthem
from myhealth_fhir.cli.output import (
    print_claim_summary,
    print_coverage_summary,
    print_organization_summary,
)
from myhealth_fhir.fhir.anthem_save import save_claims_to_db, save_eobs_to_db
from myhealth_fhir.fhir.client import get_fhir_client
from myhealth_fhir.services.auth import get_auth_manager


# ── Anthem-specific resource commands ──────────────────────────


@anthem.command(name="eob")
@click.option(
    "--patient-id", "-p", multiple=True, help="Filter by patient ID (repeatable, default: all stored patients)"
)
@click.option("--status", "-s", help="Filter by status (active, historical, entered-in-error)")
@click.option("--use", "-u", help="Filter by use (normal, conditional, information-only)")
@click.option(
    "--since",
    default=None,
    help=(
        "Date filter (YYYY-MM-DD), EOBs created since this date. "
        "On first pull defaults to YTD; subsequent pulls auto-incremental via lastupdated-since."
    ),
)
@click.option(
    "--lastupdated-since",
    default=None,
    help="Incremental load: only EOBs updated since (YYYY-MM-DD). Takes priority over --since.",
)
@click.option("--count", "-c", default=100, type=int, help="Page size (default 100)")
@click.option("--no-paginate", is_flag=True, help="Only fetch first page instead of all pages")
@click.option("--detailed", "-d", is_flag=True, help="Print individual EOB records instead of summary metrics")
@click.option("--output", "-o", help="Path to output JSONL file to save EOB records (in addition to DB)")
@click.option("--no-db", is_flag=True, help="Skip writing EOBs to the database")
@click.pass_context
def eob(ctx, patient_id, status, use, since, lastupdated_since, count, no_paginate, detailed, output, no_db):
    """List Explanation of Benefits records.

    First pull defaults to YTD. Subsequent pulls auto-incremental (only
    EOBs updated since last fetch). Writes results to the local SQLite DB
    immediately. If a patient's token has expired, kicks off re-auth.
    """
    from tqdm import tqdm

    provider = ctx.obj.get("provider", "anthem")
    client = get_fhir_client(provider)
    auth_mgr = get_auth_manager(provider)

    all_pages = not no_paginate
    patient_list = list(patient_id) if patient_id else None

    # ── Auto-detect fetch mode ────────────────────────────────────
    user_date = bool(since or lastupdated_since)
    if not user_date:
        pids = patient_list if patient_list else auth_mgr.token_store.list_patient_ids()
        fetched = {pid: auth_mgr.token_store.get_last_eob_fetch(pid) for pid in pids}
        if pids and all(v is not None for v in fetched.values()):
            earliest = min(v for v in fetched.values() if v is not None)
            lastupdated_since = earliest.strftime("%Y-%m-%d")
            click.echo(click.style(f"Incremental fetch (lastupdated >= {lastupdated_since})", fg="cyan"))
        elif pids:
            click.echo(click.style("First-time fetch (no date limit — pulling all EOBs)", fg="cyan"))

    # ── Re-auth helper ────────────────────────────────────────────
    def reauth_for_patient(pid: str) -> bool:
        click.echo(click.style(f"\nToken expired for patient {pid}. Starting re-auth flow...", bold=True, fg="yellow"))
        return do_login(provider, reason=f"re-authenticate patient {pid}")

    def fetch_single(pid, desc=""):
        """Fetch EOBs for one patient, retrying once after re-auth."""
        for attempt in range(2):
            try:
                return client.list_explanation_of_benefits(
                    patient_id=pid,
                    status=status,
                    use=use,
                    created_date_gte=None if lastupdated_since else since,
                    lastupdated_gte=lastupdated_since,
                    count=count,
                    all_pages=all_pages,
                )
            except RuntimeError:
                if attempt == 0 and reauth_for_patient(pid):
                    continue
                raise
        return None

    def update_checkpoint(pid):
        if not no_db:
            auth_mgr.token_store.set_last_eob_fetch(pid)

    # ── Fetch loop ────────────────────────────────────────────────
    if patient_list:
        results = {}
        for pid in tqdm(patient_list, desc="Fetching EOBs", unit="patient"):
            data = fetch_single(pid)
            if data is not None:
                eobs_for_patient = data if all_pages else [entry.get("resource", {}) for entry in data.get("entry", [])]
                results[pid] = eobs_for_patient
                if not no_db:
                    save_eobs_to_db(client, eobs_for_patient)
                    update_checkpoint(pid)
                    try:
                        client.collect_and_resolve_eob_entities(eobs_for_patient)
                    except Exception:
                        pass
    else:
        results = client.fetch_and_store_eobs_all_patients(
            status=status,
            use=use,
            created_date_gte=since,
            lastupdated_gte=lastupdated_since,
            no_paginate=no_paginate,
            on_auth_failure=reauth_for_patient,
        )
    date_label = (
        f"lastUpdated >= {lastupdated_since}"
        if lastupdated_since
        else f"service-date >= {since}"
        if since
        else "all time"
    )

    def patient_label(pid: str) -> str:
        token = auth_mgr.token_store.load(patient_id=pid)
        name = token.patient_name if token and token.patient_name else ""
        return f"  Patient {pid} ({name})" if name else f"  Patient {pid}"

    if patient_list:
        total_count = sum(len(v) for v in results.values())
        for pid, eobs_for_patient in results.items():
            click.echo(f"{patient_label(pid)}: {len(eobs_for_patient)} EOBs")
        click.echo(
            click.style(
                f"\nTotal EOB records fetched: {total_count} across {len(results)} patient(s) ({date_label})",
                bold=True,
                fg="green",
            )
        )
    else:
        click.echo("")
        for pid, info in tqdm(results.items(), desc="Processing results", unit="patient", leave=False):
            status_icon = click.style("ERROR", fg="red") if info["error"] else click.style("OK", fg="green")
            db_total = info.get("db_total", "?")
            click.echo(f"{patient_label(pid)}: {info['count']} new EOBs (total {db_total} in DB) [{status_icon}]")
            if info["error"]:
                click.echo(f"    └─ {info['error']}")

        successful = [r for r in results.values() if not r["error"]]
        total_delta = sum(r["count"] for r in successful)
        db_totals = [r.get("db_total", 0) for r in successful]
        failed = [r for r in results.values() if r["error"]]
        click.echo(
            click.style(
                f"\nFetched {total_delta} new EOBs — {sum(db_totals)} total across {len(successful)} patient(s) ({date_label})",
                bold=True,
                fg="green",
            )
        )
        if failed:
            click.echo(click.style(f"  Re-auth needed for {len(failed)} patient(s).", fg="yellow"))


@anthem.command(name="patients")
@click.option("--name", "-n", help="Filter by patient name")
@click.option("--birthdate", "-b", help="Filter by birthdate (YYYY-MM-DD)")
@click.option("--count", "-c", default=10, type=int, help="Number of results (max 100)")
@click.pass_context
def patients(ctx, name, birthdate, count):
    """List patient records."""
    provider = ctx.obj.get("provider", "anthem")
    client = get_fhir_client(provider)
    do_auth(provider)
    with tqdm(total=1, desc="Fetching patients") as _pbar:
        data = client.list_patients(name=name, birthdate=birthdate, count=count)
        _pbar.update(1)

    click.echo(f"\nTotal patients: {data.get('total', 'unknown')}")
    for entry in data.get("entry", []):
        resource = entry.get("resource", {})
        click.echo(click.style(f"\n--- Patient #{resource.get('id')} ---", bold=True, fg="blue"))
        names = ", ".join(
            f"{n.get('given', [])} {n.get('family', '')}"
            for n in resource.get("name", [])
            if n.get("family") or n.get("given")
        )
        click.echo(f"  Name:      {names or 'Unknown'}")
        click.echo(f"  Birthdate: {resource.get('birthDate', 'Unknown')}")
        click.echo(f"  Gender:    {resource.get('gender', 'Unknown')}")


@anthem.command(name="coverage")
@click.option("--patient-id", "-p", help="Filter by patient ID")
@click.option("--count", "-c", default=10, type=int, help="Number of results (max 100)")
@click.pass_context
def coverage(ctx, patient_id, count):
    """List insurance coverage records."""
    provider = ctx.obj.get("provider", "anthem")
    client = get_fhir_client(provider)
    do_auth(provider)
    with tqdm(total=1, desc="Fetching coverage") as _pbar:
        data = client.list_coverage(patient_id=patient_id, count=count)
        _pbar.update(1)

    click.echo(f"\nTotal coverage records: {data.get('total', 'unknown')}")
    for entry in data.get("entry", []):
        resource = entry.get("resource", {})
        click.echo(click.style(f"\n--- Coverage #{resource.get('id')} ---", bold=True, fg="magenta"))
        print_coverage_summary(resource)


@anthem.command(name="claims")
@click.option("--patient-id", "-p", help="Filter by patient ID")
@click.option("--status", "-s", help="Filter by status")
@click.option("--use", "-u", help="Filter by use (normal, conditional, information-only)")
@click.option("--since", help="Date filter (YYYY-MM-DD), claims updated since this date")
@click.option("--count", "-c", default=10, type=int, help="Number of results (max 100)")
@click.pass_context
def claims(ctx, patient_id, status, use, since, count):
    """List healthcare claim records (live FHIR query)."""
    provider = ctx.obj.get("provider", "anthem")
    client = get_fhir_client(provider)
    do_auth(provider)
    with tqdm(total=1, desc="Fetching claim records") as _pbar:
        data = client.list_claims(
            patient_id=patient_id,
            status=status,
            use=use,
            lastupdated_gte=since,
            count=count,
        )
        _pbar.update(1)

    click.echo(f"\nTotal claim records: {data.get('total', 'unknown')}")
    for entry in data.get("entry", []):
        resource = entry.get("resource", {})
        click.echo(click.style(f"\n--- Claim #{resource.get('id')} ---", bold=True, fg="yellow"))
        print_claim_summary(resource)


@anthem.command(name="fetch-claims")
@click.option(
    "--patient-id", "-p", multiple=True, help="Filter by patient ID (repeatable, default: all stored patients)"
)
@click.option("--status", "-s", help="Filter by status (active, cancelled, draft, entered-in-error)")
@click.option("--use", "-u", help="Filter by use (claim, preauthorization, predetermination)")
@click.option("--since", default=None, help="Date filter (YYYY-MM-DD), claims updated since this date. On first run defaults to all claims.")
@click.pass_context
def fetch_claims(ctx, patient_id, status, use, since):
    """Fetch and store Claim submissions for all stored patients.

    Writes Claim resources to the local SQLite DB so you can track
    submissions separately from adjudicated EOBs. Uses ``_lastUpdated``
    so claims that change status/adjudication are re-fetched. On first
    run pulls all claims; subsequent runs are incremental.
    """
    from tqdm import tqdm

    provider = ctx.obj.get("provider", "anthem")
    client = get_fhir_client(provider)
    auth_mgr = get_auth_manager(provider)

    patient_list = list(patient_id) if patient_id else None

    def reauth_for_patient(pid: str) -> bool:
        click.echo(click.style(f"\nToken expired for patient {pid}. Starting re-auth flow...", bold=True, fg="yellow"))
        return do_login(provider, reason=f"re-authenticate patient {pid}")

    if patient_list:
        results: dict[str, Any] = {}
        for pid in tqdm(patient_list, desc="Fetching claims", unit="patient"):
            token = None
            try:
                token = auth_mgr.get_valid_token(patient_id=pid)
            except RuntimeError:
                if reauth_for_patient(pid):
                    try:
                        token = auth_mgr.get_valid_token(patient_id=pid)
                    except RuntimeError as e:
                        results[pid] = {"count": 0, "error": str(e)}
                        continue
                else:
                    results[pid] = {"count": 0, "error": "Re-auth declined"}
                    continue
            if not token:
                results[pid] = {"count": 0, "error": "No token"}
                continue
            try:
                data = client.list_claims(
                    patient_id=pid,
                    status=status,
                    use=use,
                    lastupdated_gte=since,
                    count=100,
                    all_pages=True,
                )
                claims = data
                db_count = save_claims_to_db(client, claims)
                results[pid] = {"count": db_count, "error": None}
                auth_mgr.token_store.set_last_claim_fetch(pid)
            except Exception as e:
                results[pid] = {"count": 0, "error": str(e)}
    else:
        if not since:
            pids = auth_mgr.token_store.list_patient_ids()
            fetched = {pid: auth_mgr.token_store.get_last_claim_fetch(pid) for pid in pids}
            if pids and all(v is not None for v in fetched.values()):
                earliest = min(v for v in fetched.values() if v is not None)
                since = earliest.strftime("%Y-%m-%d")
                click.echo(click.style(f"Incremental fetch (lastupdated >= {since})", fg="cyan"))
            else:
                click.echo(click.style("First-time fetch (no date limit — pulling all claims)", fg="cyan"))

        results = client.fetch_and_store_claims_all_patients(
            status=status,
            use=use,
            lastupdated_gte=since,
            on_auth_failure=reauth_for_patient,
        )

    click.echo("")
    for pid, info in tqdm(results.items(), desc="Processing results", unit="patient", leave=False):
        status_icon = click.style("ERROR", fg="red") if info["error"] else click.style("OK", fg="green")
        token = auth_mgr.token_store.load(patient_id=pid)
        name = token.patient_name if token and token.patient_name else ""
        name_str = f" ({name})" if name else ""
        click.echo(f"  Patient {pid}{name_str}: {info['count']} claims [{status_icon}]")
        if info["error"]:
            click.echo(f"    └─ {info['error']}")

    successful = [r for r in results.values() if not r["error"]]
    total_count = sum(r["count"] for r in successful)
    date_desc = f"updated >= {since}" if since else "all time"
    click.echo(
        click.style(
            f"\nTotal claims stored in DB: {total_count} across {len(successful)} patient(s) ({date_desc})",
            bold=True,
            fg="green",
        )
    )


@anthem.command(name="update")
@click.option("--patient-id", "-p", multiple=True, help="Filter by patient ID (repeatable, default: all stored patients)")
@click.option("--force", "-f", is_flag=True, help="Reset checkpoints; fetches from this year (use --all for full history)")
@click.option("--all", "fetch_all", is_flag=True, help="Fetch full history for EOBs and claims (slow)")
@click.pass_context
def update(ctx, patient_id, force, fetch_all):
    """Update all Anthem data (EOBs, claims, patients, coverage).

    Fetches EOBs and claims incrementally — only resources since the
    last successful update.  Patients and coverage are always fetched
    fresh (they rarely change).

    Uses ``_lastUpdated`` (FHIR ``meta.lastUpdated``) as the anchor so
    claims that change status or adjudication are re-fetched even if
    they are not new.

    ``-f`` resets checkpoints and re-fetches from the current year.
    Add ``--all`` to ignore all date filters (very slow on large accounts).
    """
    from datetime import datetime

    provider = ctx.obj.get("provider", "anthem")
    client = get_fhir_client(provider)
    auth_mgr = get_auth_manager(provider)

    patient_list = list(patient_id) if patient_id else None
    pid_list = patient_list or auth_mgr.token_store.list_patient_ids()

    if not pid_list:
        click.echo(click.style("No patients found. Run 'auth login' first.", fg="red"))
        return

    # Fetch-mode selection:
    #   default  → per-patient auto-incremental: each patient is anchored to its
    #              OWN last-successful-fetch checkpoint; brand-new patients get a
    #              one-time full pull without forcing re-pulls for existing ones.
    #   --force  → reset: re-fetch every patient from YTD this year.
    #   --all    → full history for every patient (ignores checkpoints).
    if fetch_all:
        eob_lu = None
        claim_lu = None
        auto_incremental = False
        click.echo(click.style("  Full-history fetch (checkpoints ignored)", fg="yellow"))
    elif force:
        ytd = f"{datetime.now().year}-01-01"
        eob_lu = ytd
        claim_lu = ytd
        auto_incremental = False
        click.echo(click.style(f"  Force-reset: re-fetching from YTD ({ytd}) for all patients", fg="yellow"))
    else:
        eob_lu = None
        claim_lu = None
        auto_incremental = True
        click.echo(click.style("  Incremental per-patient fetch (uses each patient's checkpoint)", fg="cyan"))

    pbar = tqdm(total=2 + 2 * len(pid_list), desc="Fetching", unit="step")

    # ── 1. EOBs ──────────────────────────────────────────────────
    pbar.set_description("EOBs")
    try:
        eob_results = client.fetch_and_store_eobs_all_patients(
            patient_ids=pid_list,
            lastupdated_gte=eob_lu,
            auto_incremental=auto_incremental,
            on_auth_failure=lambda pid: do_login(provider, reason=f"re-auth patient {pid}"),
        )
    except Exception as e:
        click.echo(click.style(f"  EOB fetch failed: {e}", fg="red"))
        eob_results = {}

    pbar.update(1)

    # ── 2. Claims ────────────────────────────────────────────────
    pbar.set_description("Claims")
    try:
        claim_results = client.fetch_and_store_claims_all_patients(
            patient_ids=pid_list,
            lastupdated_gte=claim_lu,
            auto_incremental=auto_incremental,
            on_auth_failure=lambda pid: do_login(provider, reason=f"re-auth patient {pid}"),
        )
    except Exception as e:
        click.echo(click.style(f"  Claim fetch failed: {e}", fg="red"))
        claim_results = {}

    pbar.update(1)

    # ── 3+4. Patients & Coverage (always fresh, per patient) ────
    patient_entries: list[dict] = []
    coverage_entries: list[dict] = []
    for pid in pid_list:
        pbar.set_description("Coverage")
        try:
            cov = client.list_coverage(patient_id=pid, count=100)
            coverage_entries.extend(cov.get("entry", []))
        except Exception as e:
            click.echo(click.style(f"  Coverage fetch failed for {pid}: {e}", fg="red"))
        pbar.update(1)

        pbar.set_description("Patients")
        try:
            pat = client.list_patients(patient_id=pid, count=100)
            patient_entries.extend(pat.get("entry", []))
        except Exception as e:
            click.echo(click.style(f"  Patient fetch failed for {pid}: {e}", fg="red"))
        pbar.update(1)

    patient_data = {"total": len(patient_entries), "entry": patient_entries}
    coverage_data = {"total": len(coverage_entries), "entry": coverage_entries}
    pbar.close()

    # ── Summary ──────────────────────────────────────────────────
    def pid_name(pid: str) -> str:
        token = auth_mgr.token_store.load(patient_id=pid)
        if not token:
            return ""
        name = token.patient_name or ""
        if not name or name == pid:
            # Auto-resolve from FHIR API if name is missing or self-referencing
            try:
                patient = client.get_patient(pid)
                name = client.extract_patient_name(patient)
                if name and name != pid:
                    # Persist the resolved name to the auth DB
                    token2 = auth_mgr.token_store.load(patient_id=pid)
                    if token2:
                        token2.patient_name = name
                        auth_mgr.token_store.save(token2)
                elif not name:
                    name = ""
            except Exception:
                name = ""
        return name or ""

    click.echo("")
    click.echo(click.style("═══ Update Summary ═══", bold=True))

    # EOBs
    eob_ok = {pid: info for pid, info in eob_results.items() if not info.get("error")}
    eob_fail = {pid: info for pid, info in eob_results.items() if info.get("error")}
    if eob_ok:
        for pid, info in eob_ok.items():
            name = pid_name(pid)
            label = name or pid
            saved = info.get("saved", info.get("count", 0))
            new = info.get("new", 0)
            updated = info.get("updated", 0)
            db_total = info.get("db_total", "?")
            msg = f"EOBs:   {label}: {saved} processed ({new} new, {updated} updated) — {db_total} total in DB"
            click.echo(click.style(msg, fg="green"))
    if eob_fail:
        for pid, info in eob_fail.items():
            name = pid_name(pid)
            label = name or pid
            click.echo(click.style(f"EOBs:   {label}: FAILED — {info['error']}", fg="red"))

    # Claims
    claim_ok = {pid: info for pid, info in claim_results.items() if not info.get("error")}
    claim_fail = {pid: info for pid, info in claim_results.items() if info.get("error")}
    if claim_ok:
        for pid, info in claim_ok.items():
            name = pid_name(pid)
            label = name or pid
            saved = info.get("saved", info.get("count", 0))
            new = info.get("new", 0)
            updated = info.get("updated", 0)
            db_total = info.get("db_total", "?")
            msg = f"Claims: {label}: {saved} processed ({new} new, {updated} updated) — {db_total} total in DB"
            click.echo(click.style(msg, fg="green"))
    if claim_fail:
        for pid, info in claim_fail.items():
            name = pid_name(pid)
            label = name or pid
            click.echo(click.style(f"Claims: {label}: FAILED — {info['error']}", fg="red"))

    # Patients
    total_patients = patient_data.get("total", "unknown")
    entry_count = len(patient_data.get("entry", []))
    click.echo(click.style(f"Patients: {total_patients} available ({entry_count} shown)", fg="green"))
    for entry in patient_data.get("entry", []):
        resource = entry.get("resource", {})
        names = ", ".join(
            (" ".join(n.get("given", [])) if isinstance(n.get("given"), list) else n.get("given", "")) + " " + n.get("family", "")
            for n in resource.get("name", [])
            if n.get("family") or n.get("given")
        )
        click.echo(f"  - {names.strip() or resource.get('id', '?')}")

    # Coverage
    total_coverage = coverage_data.get("total", "unknown")
    coverage_count = len(coverage_data.get("entry", []))
    click.echo(click.style(f"Coverage: {total_coverage} available ({coverage_count} shown)", fg="green"))
    for entry in coverage_data.get("entry", []):
        resource = entry.get("resource", {})
        coding = resource.get("type", {}).get("coding", [])
        plan = coding[0].get("code", "N/A") if coding else "N/A"
        status = resource.get("status", "N/A")
        period = resource.get("period", {})
        parts = [f"[{status}]", plan]
        start = period.get("start")
        end = period.get("end")
        if start:
            parts.append(f"from {start}")
        if end:
            parts.append(f"to {end}")
        click.echo("  - " + " ".join(parts))

    # Per-patient checkpoints are updated by fetch_and_store_* on success
    # (EOB/claim separately, so a failed claims fetch never rolls back the EOB
    # checkpoint and vice versa). Nothing extra to persist here.

    if eob_ok or claim_ok:
        click.echo(click.style("\nUpdate complete. Run again to fetch only changes since this run.", fg="cyan"))


@click.option("--name", "-n", help="Filter by organization name")
@click.option("--active/--all", default=None, help="Filter by active status")
@click.option("--count", "-c", default=10, type=int, help="Number of results (max 100)")
@click.pass_context
def organizations(ctx, name, active, count):
    """List insurance/provider organizations."""
    provider = ctx.obj.get("provider", "anthem")
    client = get_fhir_client(provider)
    do_auth(provider)
    with tqdm(total=1, desc="Fetching organizations") as _pbar:
        data = client.list_organizations(name=name, active=active, count=count)
        _pbar.update(1)

    click.echo(f"\nTotal organizations: {data.get('total', 'unknown')}")
    for entry in data.get("entry", []):
        resource = entry.get("resource", {})
        click.echo(click.style(f"\n--- Organization #{resource.get('id')} ---", bold=True, fg="cyan"))
        print_organization_summary(resource)


@anthem.command(name="member-claims")
@click.option("--source", type=click.Choice(["fhir", "registry", "all"]), default="all",
              help="Filter by source ('fhir' for member/OON EOBs, 'registry' for manual submissions)")
@click.option("--patient-id", "-p", help="Filter by patient ID")
@click.pass_context
def member_claims(ctx, source, patient_id):
    """List member-submitted claims + out-of-network EOBs + registry status."""
    from sqlalchemy import text

    from myhealth_fhir.db import get_anthem_session

    where = []
    params: dict = {}
    if source != "all":
        where.append("source = :src")
        params["src"] = source
    if patient_id:
        where.append("patient_id = :pid")
        params["pid"] = patient_id
    clause = ("WHERE " + " AND ".join(where)) if where else ""

    with get_anthem_session() as session:
        rows = session.execute(text(f"""
            SELECT source, eob_id, claim_number, status, outcome, created_date,
                   patient_id, patient_name, provider_name, submission_origin,
                   is_out_of_network, total_submitted, total_member_liability,
                   total_noncovered, submission_id, portal_submission_id,
                   service_date, cpt_codes, total_amount, submission_status,
                   matched_eob_id, matched_claim_id
            FROM member_claims
            {clause}
            ORDER BY COALESCE(created_date, service_date, '1970-01-01') DESC
        """), params).all()

    click.echo(click.style(f"\nMember-submitted claims: {len(rows)}", bold=True, fg="cyan"))
    for r in rows:
        click.echo(click.style("--- " + ("#%s" % (r.eob_id or r.submission_id)), bold=True, fg="green"))
        click.echo(f"  source:    {r.source}")
        click.echo(f"  patient:   {r.patient_name} ({r.patient_id})")
        if r.claim_number:
            click.echo(f"  claim#:    {r.claim_number}")
        click.echo(f"  provider:  {r.provider_name}")
        if r.source == "fhir":
            click.echo(f"  status:    {r.status} outcome={r.outcome}")
            click.echo(f"  origin:    {r.submission_origin}  oon={r.is_out_of_network}")
            click.echo(f"  submitted: {r.total_submitted}  member_liability: "
                       f"{r.total_member_liability}  noncovered: {r.total_noncovered}")
        else:
            click.echo(f"  submission: {r.portal_submission_id}  service_date={r.service_date}  "
                       f"amount={r.total_amount}")
            click.echo(f"  status:    {r.submission_status}  matched_eob={r.matched_eob_id}  "
                       f"matched_claim={r.matched_claim_id}")
        click.echo("")


@anthem.group(name="submission")
def submission():
    """Manage the manual member-submission registry."""


@submission.command(name="add")
@click.option("--portal-id", default=None, help="Portal submission ID (e.g. 82a1c9d44e7b0f15)")
@click.option("--claim-number", default=None, help="Known claim number, if any")
@click.option("--provider", default=None, help="Provider name")
@click.option("--npi", default=None, help="Provider NPI")
@click.option("--date", "service_date", default=None, help="Service date (YYYY-MM-DD)")
@click.option("--cpt", default=None, help="Comma-separated CPT codes")
@click.option("--amount", type=float, default=None, help="Total submitted amount")
@click.option("--patient-id", "-p", default=None, help="FHIR patient ID (default: primary)")
@click.pass_context
def submission_add(ctx, portal_id, claim_number, provider, npi, service_date, cpt, amount, patient_id):
    """Register a member-submitted (paper/portal) claim."""
    from myhealth_fhir.db import get_anthem_session
    from myhealth_fhir.models.anthem import MemberClaimSubmission

    if not patient_id:
        auth_mgr = get_auth_manager(ctx.obj.get("provider", "anthem"))
        token = auth_mgr.token_store.load()
        patient_id = token.patient_id if token else None

    svc_date = None
    if service_date:
        try:
            svc_date = date.fromisoformat(service_date)
        except ValueError:
            click.echo(click.style(f"Invalid date: {service_date}", fg="red"), err=True)
            raise SystemExit(1)

    with get_anthem_session() as session:
        row = MemberClaimSubmission(
            portal_submission_id=portal_id,
            patient_id=patient_id,
            claim_number=claim_number,
            provider_ref=None,
            provider_npi=npi,
            service_date=svc_date,
            cpt_codes=cpt,
            total_amount=amount,
            status="registered",
        )
        session.add(row)
        session.flush()
        if provider:
            from myhealth_fhir.db.identity import upsert_entity_name
            row.provider_ref = upsert_entity_name(
                session, provider="anthem", entity_type="Organization",
                entity_id=f"member_submission:{row.id}:provider", name=provider,
            )
        session.commit()
        click.echo(click.style(f"Registered submission #{row.id} (status: registered)", fg="green", bold=True))


@submission.command(name="list")
@click.pass_context
def submission_list(ctx):
    """List all manual member-submission registry rows and their match status."""
    from myhealth_fhir.db import get_anthem_session
    from myhealth_fhir.models.anthem import MemberClaimSubmission

    with get_anthem_session() as session:
        rows = session.query(MemberClaimSubmission).order_by(MemberClaimSubmission.id.desc()).all()
    click.echo(click.style(f"\nRegistered submissions: {len(rows)}", bold=True, fg="cyan"))
    for r in rows:
        from myhealth_fhir.models.anthem import EntityName
        with get_anthem_session() as session:
            provider_name = session.get(EntityName, r.provider_ref).name if r.provider_ref and session.get(EntityName, r.provider_ref) else None
        click.echo(f"  #{r.id}: {r.portal_submission_id or '(no portal id)'} "
                   f"status={r.status} claim#={r.claim_number or '-'} "
                   f"provider={provider_name or '-'} date={r.service_date} "
                   f"amount={r.total_amount} matched_eob={r.matched_eob_id or '-'}")

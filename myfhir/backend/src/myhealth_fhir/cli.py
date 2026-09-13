"""Click CLI for the Anthem/Elevance Health TotalView FHIR API."""

import json
import sys
import webbrowser
import secrets
import os
from datetime import date
from urllib.parse import urlparse, parse_qs
from typing import Any

from collections import defaultdict

import click
import httpx
from tqdm import tqdm

from myhealth_fhir.config.settings import resolve_provider
from myhealth_fhir.services.auth import get_auth_manager, RefreshDaemon
from myhealth_fhir.services.fhir_client import get_fhir_client, backfill_clinical_note_attachments


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx):
    r"""MyHealth FHIR API Explorer CLI.

    \b
    Providers:
      myhealth anthem  <cmd>    Anthem/Elevance Health (insurance data)
      myhealth ucla    <cmd>    UCLA Health via Epic (clinical data)

    \b
    Quick start:
      myhealth anthem auth login          # Authenticate with Anthem
      myhealth ucla auth login            # Authenticate with UCLA
      myhealth anthem eob                 # List Explanation of Benefits
      myhealth anthem patients            # List patient records
      myhealth ucla fhir query Observation # Query lab results
      myhealth mcd                        # CMS Mandate MCD (no auth)
      myhealth formulary                  # CMS Mandate Formulary (no auth)
      myhealth server --port 8443         # Start FastAPI web server
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ── Provider Groups ─────────────────────────────────────────────


@main.group(short_help="Anthem/Elevance Health commands")
@click.option("--provider", "-p", "provider", default="anthem", type=click.Choice(["anthem"]), show_default=True)
@click.pass_context
def anthem(ctx, provider):
    """Anthem/Elevance Health commands (insurance, EOB, coverage)."""
    ctx.ensure_object(dict)
    ctx.obj["provider"] = provider


@main.group(short_help="UCLA Health commands")
@click.option("--provider", "-p", "provider", default="ucla", type=click.Choice(["ucla"]), show_default=True)
@click.pass_context
def ucla(ctx, provider):
    """UCLA Health via Epic FHIR (clinical data)."""
    ctx.ensure_object(dict)
    ctx.obj["provider"] = provider


@main.group(short_help="Database maintenance commands")
def db():
    """Database maintenance commands (backfills, migrations)."""


@db.command(name="backfill-raw-json")
@click.option("--only", type=click.Choice(["anthem", "ucla"]), default="anthem", show_default=True)
@click.option("--dry-run", is_flag=True, help="Parse and count without writing")
def db_backfill_raw_json(only, dry_run):
    """Re-parse stored raw_json and populate unpacked columns/child tables."""
    from myhealth_fhir.db.rawjson_backfill import backfill_anthem, backfill_ucla

    if only == "anthem":
        stats = backfill_anthem(dry_run=dry_run)
    else:
        stats = backfill_ucla(dry_run=dry_run)
    click.echo(f"{only}: {json.dumps(stats)}")


# ── Authentication Commands (shared by provider groups) ────────


def make_auth_group(provider_name: str = None):
    """Build and return a Click group with auth subcommands for a provider."""

    @click.group()
    def auth():
        """Authentication management (login, status, refresh, daemon)."""
        pass

    @auth.command(name="login")
    @click.pass_context
    def auth_login(ctx):
        provider = ctx.obj.get("provider", "anthem")
        if not do_login(provider):
            sys.exit(1)

    @auth.command(name="status")
    @click.pass_context
    def auth_status_cmd(ctx):
        provider = ctx.obj.get("provider", "anthem")
        do_status(provider)

    @auth.command(name="refresh")
    @click.option("--patient-id", "-p", help="Patient ID to refresh (default: all)")
    @click.pass_context
    def auth_refresh(ctx, patient_id):
        provider = ctx.obj.get("provider", "anthem")
        do_refresh(provider, patient_id)

    @auth.command(name="clear")
    @click.option("--patient-id", "-p", help="Patient ID to clear (default: all)")
    @click.pass_context
    def auth_clear(ctx, patient_id):
        provider = ctx.obj.get("provider", "anthem")
        do_clear(provider, patient_id)

    @auth.command(name="daemon")
    @click.option("--interval", "-i", default=1800, type=int, help="Seconds between refresh checks (default: 30m)")
    @click.option(
        "--threshold", "-t", default=600, type=int, help="Seconds before expiry to proactively refresh (default: 10m)"
    )
    @click.pass_context
    def auth_daemon(ctx, interval: int, threshold: int):
        provider = ctx.obj.get("provider", "anthem")
        do_daemon(provider, interval, threshold)

    @auth.command(name="backfill-names")
    @click.pass_context
    def auth_backfill_names(ctx):
        """Fetch patient names from FHIR API for all stored tokens."""
        provider = ctx.obj.get("provider", "anthem")
        mgr = get_auth_manager(provider)
        fhir_client = get_fhir_client(provider)
        pids = mgr.token_store.list_patient_ids()
        if not pids:
            click.echo("No stored patient tokens found.")
            return
        ok = 0
        for pid in tqdm(pids, desc="Resolving names", unit="patient"):
            try:
                token = mgr.token_store.load(patient_id=pid)
                if not token:
                    continue
                name = fhir_client.resolve_patient_name(pid)
                if name:
                    token.patient_name = name
                    mgr.token_store.save(token)
                    click.echo(f"  {pid} → {name}")
                    ok += 1
            except Exception as e:
                click.echo(click.style(f"  {pid}: error — {e}", fg="red"))
        click.echo(click.style(f"\nResolved {ok}/{len(pids)} patient name(s).", bold=True, fg="green"))

    @auth.command(name="backfill-entities")
    @click.pass_context
    def auth_backfill_entities(ctx):
        """Resolve entity names (providers, practitioners) from FHIR for all stored EOBs."""
        provider = ctx.obj.get("provider", "anthem")
        fhir_client = get_fhir_client(provider)
        from myhealth_fhir.db import get_anthem_session
        from sqlalchemy import text

        with get_anthem_session() as s:
            refs = (
                s.execute(
                    text("""
                SELECT DISTINCT provider_ref FROM eob WHERE provider_ref IS NOT NULL
                UNION
                SELECT DISTINCT payee_ref FROM eob WHERE payee_ref IS NOT NULL
                UNION
                SELECT DISTINCT provider_ref FROM eob_care_team WHERE provider_ref IS NOT NULL
            """)
                )
                .scalars()
                .all()
            )
        if not refs:
            click.echo("No entity references found in EOB data.")
            return
        click.echo(f"Resolving {len(refs)} entity reference(s)...")
        resolved = fhir_client.resolve_and_cache_entity_names(set(refs))
        for eid, name in resolved.items():
            click.echo(f"  {eid} → {name}")
        click.echo(click.style(f"\nResolved {len(resolved)}/{len(refs)} entity name(s).", bold=True, fg="green"))

    return auth


def make_fhir_group(provider_name: str = None):
    """Build and return a Click group with generic FHIR subcommands for a provider."""

    @click.group()
    def fhir():
        """FHIR resource operations."""
        pass

    @fhir.command(name="metadata")
    @click.pass_context
    def fhir_metadata(ctx):
        """Return the FHIR server capability statement."""
        provider = ctx.obj.get("provider", "anthem")
        client = get_fhir_client(provider)
        do_auth(provider)
        with tqdm(total=1, desc="Fetching metadata") as _pbar:
            data = client.meta()
            _pbar.update(1)
        click.echo(json.dumps(data, indent=2))

    @fhir.command(name="query")
    @click.argument("resource_type")
    @click.option("--param", "-p", multiple=True, help="Search parameter (format: key=value)")
    @click.pass_context
    def fhir_query(ctx, resource_type: str, param: tuple[str, ...]):
        provider = ctx.obj.get("provider", "anthem")
        client = get_fhir_client(provider)
        do_auth(provider)
        params = dict(p.split("=", 1) for p in param) if param else None
        with tqdm(total=1, desc=f"Searching {resource_type}") as _pbar:
            data = client.search(resource_type, params)
            _pbar.update(1)
        click.echo(f"\nTotal results: {data.get('total', 'unknown')}")
        for entry in data.get("entry", []):
            resource = entry.get("resource", {})
            click.echo(click.style(f"\n--- {resource.get('resourceType')} ---", bold=True))
            click.echo(json.dumps(resource, indent=2))

    @fhir.command(name="get")
    @click.argument("resource_type")
    @click.argument("resource_id")
    @click.pass_context
    def fhir_get(ctx, resource_type: str, resource_id: str):
        provider = ctx.obj.get("provider", "anthem")
        client = get_fhir_client(provider)
        do_auth(provider)
        data = client.get(resource_type, resource_id)
        click.echo(json.dumps(data, indent=2))

    return fhir


anthem.add_command(make_auth_group())
anthem.add_command(make_fhir_group())
ucla.add_command(make_auth_group())
ucla.add_command(make_fhir_group())


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
                    client.save_eobs_to_db(eobs_for_patient)
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
                db_count = client.save_claims_to_db(claims)
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
    from myhealth_fhir.db import get_anthem_session
    from sqlalchemy import text

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
    from datetime import date
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


# ── UCLA-specific resource commands ────────────────────────────


@ucla.command(name="patients")
@click.option("--name", "-n", help="Filter by patient name")
@click.option("--count", "-c", default=10, type=int, help="Number of results (max 100)")
@click.pass_context
def ucla_patients(ctx, name, count):
    """List patient records."""
    provider = ctx.obj.get("provider", "ucla")
    client = get_fhir_client(provider)
    do_auth(provider)
    with tqdm(total=1, desc="Fetching patients") as _pbar:
        data = client.list_patients(name=name, count=count)
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





@ucla.command(name="save-labs")
@click.option("--patient-id", "-p", multiple=True, help="Patient ID (repeatable). All patients if omitted.")
@click.option("--date-since", help="Only save reports from this date (YYYY-MM-DD)")
@click.option("--date-until", help="Only save reports until this date (YYYY-MM-DD)")
@click.option("--category", "-c", multiple=True, default=["lab"],
              help="Filter by category (default: lab. Use --category all for everything. "
                   "Repeatable: --category lab --category imaging")
@click.option("--no-db", is_flag=True, help="Fetch and print only, don't save to DB")
@click.option("--detailed", "-d", is_flag=True, help="Print full panel details with results")
@click.pass_context
def ucla_save_labs(ctx, patient_id, date_since, date_until, category, no_db, detailed):
    """Fetch lab panels, print results, and save to the database.

    By default fetches for ALL stored patients. Use -p for specific patient(s).

    Paginates through all DiagnosticReport resources with a progress bar,
    fetches child Observations, and persists everything to PostgreSQL.

    Categories: lab, imaging, procedure, etc. Use --category all for everything
    (Epic workaround — drops category filter entirely).

    Examples:
      myhealth ucla save-labs                         # All patients, labs only
      myhealth ucla save-labs -c all                  # All categories
      myhealth ucla save-labs -p PID1 -p PID2         # Specific patients
      myhealth ucla save-labs --no-db                  # Dry run — no DB write
      myhealth ucla save-labs --no-db --detailed        # Full detail, no save
      myhealth ucla save-labs --date-since 2024-01-01
    """
    provider = ctx.obj.get("provider", "ucla")
    client = get_fhir_client(provider)
    do_auth(provider)

    auth_mgr = client.get_auth_manager()

    # Build search params
    params = {"_sort": "-date", "_count": 100}
    if category and "all" not in category:
        params["category"] = list(category) if len(category) > 1 else category[0]
    date_filters = []
    if date_since:
        date_filters.append(f"ge{date_since}")
    if date_until:
        date_filters.append(f"le{date_until}")
    if date_filters:
        params["date"] = date_filters if len(date_filters) > 1 else date_filters[0]

    def patient_label(pid: str) -> str:
        token = auth_mgr.token_store.load(patient_id=pid)
        name = token.patient_name if token and token.patient_name else ""
        return f"Patient {pid} ({name})" if name else f"Patient {pid}"

    # ── Dry-run mode (--no-db): fetch and print only ────────────────
    if no_db:
        if patient_id:
            pids = list(patient_id)
        else:
            pids = auth_mgr.token_store.list_patient_ids()
            if not pids:
                token = auth_mgr.token_store.load()
                if token and token.patient_id:
                    pids = [token.patient_id]

        for pid in (pbar_pat := tqdm(pids, desc="Fetching labs", unit="patient")):
            try:
                token = auth_mgr.get_valid_token(patient_id=pid)
            except RuntimeError:
                click.echo(click.style(f"  {patient_label(pid)}: token expired — skip", fg="yellow"))
                continue

            headers = {
                "Authorization": f"Bearer {token.access_token}",
                "Accept": "application/fhir+json",
            }

            search_params = dict(params)
            search_params.setdefault("_include", "DiagnosticReport:result")
            query_parts = "&".join(
                f"{k}={v}" if isinstance(v, str)
                else f"{k}={'&'.join(str(x) for x in v) if isinstance(v, (list, tuple)) else v}"
                for k, v in search_params.items()
            )
            url = f"{client.base_url}/DiagnosticReport?{query_parts}&patient={pid}"

            all_entries = []
            try:
                resp = httpx.get(url, headers=headers, timeout=120)
                resp.raise_for_status()
                bundle = resp.json()
                all_entries.extend(bundle.get("entry", []))
                while True:
                    links = bundle.get("link", [])
                    next_link = next((l for l in links if l.get("relation") == "next"), None)
                    if not next_link:
                        break
                    nurl = next_link.get("url", "")
                    if not nurl:
                        break
                    resp2 = httpx.get(nurl, headers=headers, timeout=120)
                    resp2.raise_for_status()
                    bundle = resp2.json()
                    all_entries.extend(bundle.get("entry", []))
            except Exception as e:
                click.echo(click.style(f"  {patient_label(pid)}: fetch failed: {e}", fg="red"))
                continue

            seen = set()
            unique = []
            for e in all_entries:
                res = e.get("resource", {})
                rid = res.get("id", "")
                if rid and rid not in seen:
                    seen.add(rid)
                    unique.append(e)

            panels = []
            observations = []
            for entry in unique:
                resource = entry.get("resource", {})
                if resource.get("resourceType") == "DiagnosticReport":
                    panels.append(resource)
                elif resource.get("resourceType") == "Observation":
                    observations.append(resource)

            obs_by_panel = {}
            for obs in observations:
                part_of = obs.get("partOf", {})
                if isinstance(part_of, dict):
                    ref = part_of.get("reference", "")
                    panel_id_key = ref.split("/")[-1] if "/" in ref else ref
                else:
                    panel_id_key = str(part_of) if part_of else ""
                obs_by_panel.setdefault(panel_id_key, []).append(obs)

            click.echo(
                click.style(
                    f"\n{patient_label(pid)}: {len(panels)} panels ({bundle.get('total', '?')} on server)",
                    bold=True, fg="cyan",
                )
            )

            if detailed:
                for panel in panels:
                    panel_obs = obs_by_panel.get(panel.get("id", ""), [])
                    print_lab_panel(panel, include_observations=True, detailed=True,
                                    child_observations=panel_obs, fetch_client=client)
            else:
                click.echo(f"  {len(observations)} observations across {len(panels)} panels")

        return

    # ── Fetch + Save to DB ───────────────────────────────────────────
    from myhealth_fhir.db import init_db
    init_db()

    if patient_id:
        pids = list(patient_id)
        results = {}
        for pid in tqdm(pids, desc="Fetching labs", unit="patient"):
            single_result = client.fetch_and_store_labs_all_patients(
                patient_ids=[pid], **params
            )
            results[pid] = single_result.get(pid, {"panels": 0, "results": 0, "imaging": 0, "error": "Unknown"})
    else:
        results = client.fetch_and_store_labs_all_patients(**params)

    # ── Print summary ────────────────────────────────────────────────
    click.echo("")
    for pid, info in tqdm(results.items(), desc="Processing results", unit="patient", leave=False):
        status_icon = click.style("ERROR", fg="red") if info.get("error") else click.style("OK", fg="green")
        click.echo(
            f"  {patient_label(pid)}: "
            f"{info.get('panels', 0)} panels, "
            f"{info.get('results', 0)} results, "
            f"{info.get('imaging', 0)} imaging obs "
            f"[{status_icon}]"
        )
        if info.get("error"):
            click.echo(f"    └─ {info['error']}")

    successful = [r for r in results.values() if not r.get("error")]
    total_panels = sum(r.get("panels", 0) for r in successful)
    total_results = sum(r.get("results", 0) for r in successful)
    total_imaging = sum((r.get("imaging") or 0) for r in successful)
    failed = [r for r in results.values() if r.get("error")]

    click.echo(
        click.style(
            f"\nSaved {total_panels} panels, {total_results} results, "
            f"{total_imaging} imaging obs across {len(successful)} patient(s)",
            bold=True, fg="green",
        )
    )
    if failed:
        click.echo(click.style(f"  Re-auth needed for {len(failed)} patient(s).", fg="yellow"))


@ucla.command(name="save-all")
@click.option("--patient-id", "-p", multiple=True, help="Patient ID (repeatable). All patients if omitted.")
@click.option("--no-db", is_flag=True, help="Fetch and print only, don't save to DB")
@click.option("--skip-labs", is_flag=True, help="Skip lab/imaging data (faster if already fetched)")
@click.option("--detailed", "-d", is_flag=True, help="Show per-resource-type detail")
@click.option("--wipe", is_flag=True, help="TRUNCATE all UCLA app-data tables before fetching (full refresh)")
@click.pass_context
def ucla_save_all(ctx, patient_id, no_db, skip_labs, detailed, wipe):
    """Fetch ALL clinical data for ALL patients and persist to PostgreSQL.

    Fetches: encounters, conditions, procedures, medications, allergies,
    immunizations, care plans, documents, family history, vitals, labs,
    medication administrations, service requests, specimens, and communications.

    By default fetches for ALL stored patients. Use -p for specific patient(s).

    Examples:
      myhealth ucla save-all                  # All patients, all resource types
      myhealth ucla save-all -p PID            # Specific patient
      myhealth ucla save-all --skip-labs       # Skip lab/imaging (already fetched)
      myhealth ucla save-all --no-db           # Dry run — fetch & print only
      myhealth ucla save-all --wipe            # TRUNCATE tables first, then full fetch
    """
    provider = ctx.obj.get("provider", "ucla")
    client = get_fhir_client(provider)
    do_auth(provider)

    auth_mgr = client.get_auth_manager()

    if wipe and not no_db:
        from myhealth_fhir.db import get_ucla_session

        from sqlalchemy import text

        tables = [
            "lab_result", "diagnostic_report", "imaging_observation",
            "clinical_observation", "clinical_note", "medication_administration",
            "service_request", "specimen", "communication", "care_team",
            "encounter_participant", "encounter", "condition", "procedure_record",
            "medication_statement", "medication_request", "allergy_intolerance",
            "immunization", "care_plan", "document_reference", "family_member_history",
        ]
        if not click.confirm(
            f"TRUNCATE {len(tables)} UCLA app-data tables and re-fetch? "
            "(oauth_tokens / patients / entity_names preserved)"
        ):
            click.echo("Aborted.")
            ctx.exit(0)
        with get_ucla_session() as session:
            for table in tables:
                session.execute(text(f"TRUNCATE TABLE {table} CASCADE"))
            session.commit()
        click.echo(click.style(f"Wiped {len(tables)} tables.", bold=True))
    provider = ctx.obj.get("provider", "ucla")
    client = get_fhir_client(provider)
    do_auth(provider)

    auth_mgr = client.get_auth_manager()

    def patient_label(pid: str) -> str:
        token = auth_mgr.token_store.load(patient_id=pid)
        name = token.patient_name if token and token.patient_name else ""
        return f"Patient {pid} ({name})" if name else f"Patient {pid}"

    def reauth_for_patient(pid: str) -> bool:
        click.echo(click.style(f"\nToken expired for patient {pid}. Starting re-auth flow...", bold=True, fg="yellow"))
        return do_auth(provider)

    if no_db:
        # ── Dry-run: fetch only, print per-patient summary ──────
        if patient_id:
            pids = list(patient_id)
        else:
            pids = auth_mgr.token_store.list_patient_ids()
            if not pids:
                token = auth_mgr.token_store.load()
                if token and token.patient_id:
                    pids = [token.patient_id]

        resource_types = [
            "Encounter", "Condition", "Procedure", "MedicationStatement",
            "MedicationRequest", "AllergyIntolerance", "Immunization",
            "CarePlan", "DocumentReference", "FamilyMemberHistory",
        ]
        if not skip_labs:
            resource_types.extend(["DiagnosticReport", "Observation"])

        import httpx
        for pid in (pbar_pat := tqdm(pids, desc="Fetching all data", unit="patient")):
            try:
                token = auth_mgr.get_valid_token(patient_id=pid)
            except RuntimeError:
                click.echo(click.style(f"  {patient_label(pid)}: token expired — skip", fg="yellow"))
                continue

            headers = {
                "Authorization": f"Bearer {token.access_token}",
                "Accept": "application/fhir+json",
            }

            click.echo(click.style(f"\n{patient_label(pid)}:", bold=True))
            for rt in resource_types:
                url = f"{client.base_url}/{rt}?_count=1&patient={pid}"
                try:
                    resp = httpx.get(url, headers=headers, timeout=30)
                    resp.raise_for_status()
                    bundle = resp.json()
                    total = bundle.get("total", "?")
                    click.echo(f"  {rt}: {total}")
                except Exception as e:
                    click.echo(f"  {rt}: error — {e}")
        return

    # ── Fetch + Save to DB ──────────────────────────────────────
    if patient_id:
        pids = list(patient_id)
        results = {}
        for pid in tqdm(pids, desc="Fetching all data", unit="patient"):
            single = client.fetch_and_store_all_clinical_data(
                patient_ids=[pid], skip_labs=skip_labs,
            )
            results[pid] = single.get(pid, {})
    else:
        results = client.fetch_and_store_all_clinical_data(
            skip_labs=skip_labs, on_auth_failure=reauth_for_patient,
        )

    # ── Print summary ───────────────────────────────────────────
    click.echo("")
    for pid, info in tqdm(results.items(), desc="Processing results", unit="patient", leave=False):
        if "error" in info:
            click.echo(f"  {patient_label(pid)}: {click.style(info['error'], fg='red')}")
            continue
        if detailed:
            click.echo(click.style(f"\n{patient_label(pid)}:", bold=True))
            for rt, counts in sorted(info.items()):
                if isinstance(counts, dict):
                    click.echo(f"  {rt}: {counts.get('new', 0)} new, {counts.get('total', 0)} total ({counts.get('sec', '?')}s)")
        else:
            parts = []
            for k, v in sorted(info.items()):
                if isinstance(v, dict):
                    if v.get("error"):
                        parts.append(f"{k}: {click.style('error', fg='red')}")
                    else:
                        parts.append(f"{k}: {v.get('total', '?')}")
            click.echo(f"  {patient_label(pid)}: {', '.join(parts)}")

    click.echo(click.style("\nDone.", bold=True, fg="green"))


@ucla.command(name="medications")
@click.option("--patient-id", "-p", help="Patient ID")
@click.option("--count", "-c", default=20, type=int, help="Number of results (max 100)")
@click.pass_context
def ucla_medications(ctx, patient_id, count):
    """List MedicationStatement records."""
    provider = ctx.obj.get("provider", "ucla")
    client = get_fhir_client(provider)
    do_auth(provider)
    params = {"_count": count}
    if patient_id:
        params["patient"] = patient_id

    with tqdm(total=1, desc="Fetching medications") as _pbar:
        data = client.search("MedicationStatement", params)
        _pbar.update(1)

    click.echo(
        f"\nTotal medications (this page): {len(data.get('entry', []))} (server total: {data.get('total', 'unknown')})"
    )
    for entry in data.get("entry", []):
        resource = entry.get("resource", {})
        print_medication_summary(resource)


@ucla.command(name="encounters")
@click.option("--date-since", help="Filter by date (YYYY-MM-DD) or modifier (e.g., 2026-01-01)")
@click.option("--patient-id", "-p", help="Patient ID")
@click.option("--count", "-c", default=20, type=int, help="Number of results (max 100)")
@click.pass_context
def ucla_encounters(ctx, date_since, patient_id, count):
    """List patient encounters."""
    provider = ctx.obj.get("provider", "ucla")
    client = get_fhir_client(provider)
    do_auth(provider)
    params = {"_count": count}
    if date_since:
        params["date"] = f"ge{date_since}"
    if patient_id:
        params["patient"] = patient_id

    with tqdm(total=1, desc="Fetching encounters") as _pbar:
        data = client.search("Encounter", params)
        _pbar.update(1)

    click.echo(
        f"\nTotal encounters (this page): {len(data.get('entry', []))} (server total: {data.get('total', 'unknown')})"
    )
    for entry in data.get("entry", []):
        resource = entry.get("resource", {})
        print_encounter_summary(resource)


@ucla.command(name="conditions")
@click.option("--patient-id", "-p", help="Patient ID")
@click.option(
    "--clinical-status",
    "-s",
    type=click.Choice(["active", "recurrence", "resolution", "removed"]),
    help="Filter by clinical status",
)
@click.option("--count", "-c", default=20, type=int, help="Number of results (max 100)")
@click.pass_context
def ucla_conditions(ctx, patient_id, clinical_status, count):
    """List diagnosed conditions."""
    provider = ctx.obj.get("provider", "ucla")
    client = get_fhir_client(provider)
    do_auth(provider)
    params = {"_count": count}
    if patient_id:
        params["patient"] = patient_id
    if clinical_status:
        params["clinical-status"] = clinical_status

    with tqdm(total=1, desc="Fetching conditions") as _pbar:
        data = client.search("Condition", params)
        _pbar.update(1)

    click.echo(
        f"\nTotal conditions (this page): {len(data.get('entry', []))} (server total: {data.get('total', 'unknown')})"
    )
    for entry in data.get("entry", []):
        resource = entry.get("resource", {})
        print_condition_summary(resource)


@ucla.command(name="allergies")
@click.option("--patient-id", "-p", help="Patient ID")
@click.option(
    "--clinical-status", "-s", type=click.Choice(["active", "inactive", "resolved"]), help="Filter by clinical status"
)
@click.option("--count", "-c", default=20, type=int, help="Number of results (max 100)")
@click.pass_context
def ucla_allergies(ctx, patient_id, clinical_status, count):
    """List allergy and intolerance records."""
    provider = ctx.obj.get("provider", "ucla")
    client = get_fhir_client(provider)
    do_auth(provider)
    params = {"_count": count}
    if patient_id:
        params["patient"] = patient_id
    if clinical_status:
        params["clinical-status"] = clinical_status

    with tqdm(total=1, desc="Fetching allergies") as _pbar:
        data = client.search("AllergyIntolerance", params)
        _pbar.update(1)

    click.echo(
        f"\nTotal allergies (this page): {len(data.get('entry', []))} (server total: {data.get('total', 'unknown')})"
    )
    for entry in data.get("entry", []):
        resource = entry.get("resource", {})
        print_allergy_summary(resource)


@ucla.command(name="immunizations")
@click.option("--date-since", help="Filter by date (YYYY-MM-DD) or modifier")
@click.option("--patient-id", "-p", help="Patient ID")
@click.option("--count", "-c", default=20, type=int, help="Number of results (max 100)")
@click.pass_context
def ucla_immunizations(ctx, date_since, patient_id, count):
    """List immunization records."""
    provider = ctx.obj.get("provider", "ucla")
    client = get_fhir_client(provider)
    do_auth(provider)
    params = {"_count": count}
    if date_since:
        params["date"] = f"ge{date_since}"
    if patient_id:
        params["patient"] = patient_id

    with tqdm(total=1, desc="Fetching immunizations") as _pbar:
        data = client.search("Immunization", params)
        _pbar.update(1)

    click.echo(
        f"\nTotal immunizations (this page): "
        f"{len(data.get('entry', []))} (server total: {data.get('total', 'unknown')})"
    )
    for entry in data.get("entry", []):
        resource = entry.get("resource", {})
        print_immunization_summary(resource)


@ucla.command(name="careplans")
@click.option("--patient-id", "-p", help="Patient ID")
@click.option("--status", "-s", help="Filter by status (active, completed, entered-in-error, unknown)")
@click.option("--count", "-c", default=20, type=int, help="Number of results (max 100)")
@click.pass_context
def ucla_careplans(ctx, patient_id, status, count):
    """List care plans."""
    provider = ctx.obj.get("provider", "ucla")
    client = get_fhir_client(provider)
    do_auth(provider)
    params = {"_count": count}
    if patient_id:
        params["patient"] = patient_id
    if status:
        params["status"] = status

    with tqdm(total=1, desc="Fetching care plans") as _pbar:
        data = client.search("CarePlan", params)
        _pbar.update(1)

    click.echo(
        f"\nTotal care plans (this page): {len(data.get('entry', []))} (server total: {data.get('total', 'unknown')})"
    )
    for entry in data.get("entry", []):
        resource = entry.get("resource", {})
        print_careplan_summary(resource)


# ── EHI export import ──────────────────────────────────────────


@ucla.command(name="backfill-note-attachments")
@click.option("--batch-size", default=25, show_default=True, type=click.IntRange(min=1))
@click.option("--limit", default=None, type=click.IntRange(min=1), help="Process only the first N missing notes.")
@click.option("--workers", default=8, show_default=True, type=click.IntRange(min=1, max=16))
@click.pass_context
def ucla_backfill_note_attachments(ctx, batch_size, limit, workers):
    """Backfill missing clinical-note HTML and RTF from the UCLA FHIR API."""
    provider = ctx.obj.get("provider", "ucla")
    client = get_fhir_client(provider)
    click.echo("Backfilling missing clinical-note attachments from FHIR…")
    stats = backfill_clinical_note_attachments(client, provider=provider, batch_size=batch_size, limit=limit, workers=workers)
    click.echo(
        f"Processed {stats['notes']} notes; updated {stats['updated']} "
        f"({stats['html']} HTML, {stats['rtf']} RTF); failures: {stats['failed']}"
    )


# ── EHI export import ──────────────────────────────────────────


@ucla.command(name="ehi")
@click.argument("export-dir", type=click.Path(exists=True, file_okay=False, dir_okay=True))
@click.option("--patient-id", "-p", required=True, help="Patient FHIR ID (from `myhealth ucla patients`)")
@click.option("--no-db", is_flag=True, help="Dry run — report what tables/rows would be imported, write nothing")
@click.option("--only", type=click.Choice(["all", "notes", "mar", "encounters", "vitals", "orders", "immunizations"]),
              default="all", help="Import only a specific dataset (default: all)")
def ucla_ehi(export_dir, patient_id, no_db, only):
    """Import an Epic EHI (Request an Electronic Health Information) export.

    Reads the manually-downloaded Epic EHI export directory — the one with
    ``EHITables/``, ``Rich Text/`` and ``Media/`` folders — and merges the data
    that the live UCLA FHIR API does not expose: clinical note bodies, MAR
    medication administrations, encounter backfill, flowsheet vitals, and orders.

    EXCLUDED (never stored): Media/ binaries (PDFs, images, audio) and the
    CLARITY_EDG dictionary. Note bodies are parsed from Rich Text to plain text.

    The export is PHI — keep it out of any git repository. This command reads
    from the path you pass at runtime only.

    Examples:
      myhealth ucla ehi /path/to/export --patient-id PID
      myhealth ucla ehi /path/to/export -p PID --only notes
      myhealth ucla ehi /path/to/export -p PID --no-db
    """
    from myhealth_fhir.services.ehi_importer import import_ehi_export

    datasets = {
        "notes": dict(notes=True, mar=False, encounters=False, vitals=False, orders=False, imm=False),
        "mar": dict(notes=False, mar=True, encounters=False, vitals=False, orders=False, imm=False),
        "encounters": dict(notes=False, mar=False, encounters=True, vitals=False, orders=False, imm=False),
        "vitals": dict(notes=False, mar=False, encounters=False, vitals=True, orders=False, imm=False),
        "orders": dict(notes=False, mar=False, encounters=False, vitals=False, orders=True, imm=False),
        "immunizations": dict(notes=False, mar=False, encounters=False, vitals=False, orders=False, imm=True),
        "all": dict(notes=True, mar=True, encounters=True, vitals=True, orders=True, imm=True),
    }[only]

    click.echo(click.style(f"\nImporting EHI export from {export_dir}", bold=True))
    click.echo(f"  Patient: {patient_id}", nl=False)
    click.echo("  | Dry run (no DB writes): " + ("yes" if no_db else "no"))

    summary = import_ehi_export(
        export_dir, patient_id, no_db=no_db, **datasets,
    )

    click.echo(click.style("\nImport summary:", bold=True))
    total = 0
    for label, count in summary.items():
        click.echo(f"  {label}: {count}")
        total += count
    click.echo(click.style(f"\nDone. {total} rows processed.", bold=True, fg="green"))


# ── Global unauthenticated commands ────────────────────────────


@ucla.command(name="report")
@click.option("--patient-id", "-p", required=True, help="Patient FHIR ID")
@click.option("--patient-name", default="Patient", help="Patient Full Name")
@click.option("--mrn", default="N/A", help="Medical Record Number")
@click.option("--specialty", default="endocrinology", help="Clinical specialty filter")
@click.option("--output", "-o", default="patient-medical-report.docx", help="Output DOCX filepath")
def ucla_report(patient_id, patient_name, mrn, specialty, output):
    """Generate a clean, sanitized physician-facing medical packet (.docx)."""
    from myhealth_fhir.services.report_generator import generate_sanitized_report
    click.echo(click.style(f"Generating sanitized {specialty} packet for {patient_name} (MRN: {mrn})...", fg="cyan", bold=True))
    docx_path = generate_sanitized_report(patient_name, mrn, patient_id, specialty, output)
    click.echo(click.style(f"\n[SUCCESS] Physician report generated: {docx_path}", fg="green", bold=True))


@main.command(name="mcd")
@click.option("--since/--updated-gte", help="Filter by last updated date (YYYY-MM-DD)")
def mcd(since: str | None):
    """Query CMS Mandate MCD (Mental Health & Substance Use)."""
    client = get_fhir_client("anthem")
    with tqdm(total=1, desc="Fetching MCD data") as _pbar:
        data = client.cms_mandate_mcd(lastupdated_gte=since)
        _pbar.update(1)

    click.echo(f"\nTotal MCD entries: {data.get('total', 'unknown')}")
    for entry in data.get("entry", []):
        resource = entry.get("resource", {})
        click.echo(click.style(f"\n--- MCD #{resource.get('id')} ---", bold=True, fg="green"))
        click.echo(json.dumps(resource, indent=2))


@main.command(name="formulary")
@click.option("--drug", "-d", help="Filter by drug name")
@click.option("--count", "-c", default=10, type=int, help="Number of results (max 100)")
def formulary(drug: str | None, count: int):
    """Query CMS Mandate Formulary (Drug Formulary)."""
    client = get_fhir_client("anthem")
    with tqdm(total=1, desc="Fetching formulary data") as _pbar:
        data = client.cms_mandate_formulary(drug=drug, count=count)
        _pbar.update(1)

    click.echo(f"\nTotal formulary entries: {data.get('total', 'unknown')}")
    for entry in data.get("entry", []):
        resource = entry.get("resource", {})
        click.echo(click.style(f"\n--- Formulary #{resource.get('id')} ---", bold=True, fg="green"))
        click.echo(json.dumps(resource, indent=2))


@main.command(name="job")
@click.option("--daemon", "-d", is_flag=True, help="Run continuously (refresh tokens + pull on an interval)")
@click.option(
    "--interval-minutes",
    "-i",
    default=None,
    type=int,
    help="Daemon pull interval in minutes (default: JOB_INTERVAL_MINUTES env or 360)",
)
@click.option(
    "--provider",
    "-p",
    "providers",
    multiple=True,
    help="Restrict to specific provider(s); repeatable. Default: all configured providers",
)
@click.option("--skip-labs", is_flag=True, help="Skip lab/clinical pull for Epic providers")
def job(daemon, interval_minutes, providers, skip_labs):
    """Run the data-pull job (EOBs/claims for Anthem, labs/clinical for Epic providers)."""
    from myhealth_fhir.config.settings import list_providers
    from myhealth_fhir.job import job_loop, run_job_once

    if not providers:
        providers = list_providers()
    else:
        providers = list(providers)

    if interval_minutes is None:
        interval_minutes = int(os.environ.get("JOB_INTERVAL_MINUTES", "360"))

    if daemon:
        click.echo(
            click.style(
                f"Starting job daemon: providers={', '.join(providers)} interval={interval_minutes}m",
                fg="green",
                bold=True,
            )
        )
        click.echo("Press Ctrl+C to stop.\n")
        job_loop(interval_minutes=interval_minutes, providers=providers)
        return

    click.echo(click.style(f"Running data pull for {', '.join(providers)}...", bold=True))
    for provider, summary in run_job_once(providers=providers, skip_labs=skip_labs).items():
        status = summary["status"]
        color = "green" if status == "success" else "red"
        counts = summary.get("counts", {})
        new_total = counts.get("new_total", 0)
        totals = counts.get("totals_after", {})
        click.echo(click.style(f"\n[{provider}] {status.upper()}", fg=color, bold=True))
        click.echo(f"  New records: {new_total}")
        for table, count in sorted(totals.items()):
            click.echo(f"  {table}: {count}")
        if counts.get("error"):
            click.echo(click.style(f"  Error: {counts['error']}", fg="red"))


@main.command(name="server")
@click.option("--port", "-p", default=8443, type=int, help="Port to listen on")
def server(port: int):
    """Start the FastAPI web server."""
    import uvicorn

    click.echo(f"Starting server on http://localhost:{port}")
    click.echo(f"Swagger UI: http://localhost:{port}/docs")
    uvicorn.run(
        "myhealth_fhir.main:app",
        host="127.0.0.1",
        port=port,
        log_level="info",
    )


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


# ── Auth action helpers ─────────────────────────────────────────


def do_login(provider: str, reason: str = "authenticate") -> bool:
    """Run the interactive OAuth login flow.

    If *reason* is set (e.g. "re-authenticate patient P"), skips the
    "already authenticated" check and always shows the login URL.
    Returns True if exchange succeeded, False on failure.
    """
    mgr = get_auth_manager(provider)
    config = resolve_provider(provider)

    if reason and not reason.startswith("authenticate"):
        pass  # forced re-auth — skip checks
    else:
        pid_count = mgr.token_store.count()
        click.echo(click.style(f"Stored tokens: {pid_count} patient(s)", fg="cyan"))

    state = secrets.token_urlsafe(16)
    authorize_url = mgr.build_authorize_url(state=state)
    display_name = config.display_name

    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        click.echo(click.style(f"\n[Step 1] Opening browser to {display_name} login...", bold=True, fg="yellow"))
        click.echo(f"  {authorize_url}")
        try:
            webbrowser.open(authorize_url)
        except Exception:
            pass
    else:
        click.echo(
            click.style(
                f"\n[Step 1] Open this URL in your browser to log in to {display_name}:", bold=True, fg="yellow"
            )
        )
        click.echo(f"  {authorize_url}")

    raw_code = click.prompt(
        click.style("\n[Step 2] Paste the full redirect URL from your browser address bar", fg="cyan")
    )
    code = extract_code(raw_code)

    try:
        token = mgr.exchange_code(code)
    except httpx.HTTPStatusError as e:
        detail = e.response.json() if e.response.content else {}
        click.echo(click.style(f"\n[ERROR] Token exchange failed: {detail}", fg="red"))
        return False
    except Exception as e:
        click.echo(click.style(f"\n[ERROR] {e}", fg="red"))
        return False

    pid = token.patient_id or "default"
    name_str = ""
    if pid and pid != "_default" and pid != "default":
        try:
            fhir_client = get_fhir_client(provider)
            name = fhir_client.resolve_patient_name(pid)
            if name:
                token.patient_name = name
                name_str = f" ({name})"
                mgr.token_store.save(token)
        except Exception:
            pass

    click.echo(click.style(f"\n[SUCCESS] {display_name} authentication complete!", bold=True, fg="green"))
    click.echo(click.style(f"  Patient: {pid}{name_str}", fg="cyan"))
    print_token_status(token)
    return True


def do_status(provider: str) -> None:
    """Print the authentication status for a provider's stored tokens."""
    mgr = get_auth_manager(provider)
    info = mgr.status()
    if not info["authenticated"]:
        click.echo(click.style(info["message"], fg="yellow"))
        return

    click.echo(click.style(f"{provider.upper()} Authentication Status", bold=True))
    click.echo(f"  Patients: {info.get('patient_count', 0)}")

    for p in info.get("patients", []):
        pid = p.get("patient_id") or "default"
        name = p.get("patient_name") or ""
        name_str = f" ({name})" if name else ""
        exp_str = click.style("EXPIRED", fg="red") if p["expired"] else click.style("valid", fg="green")
        rexp_str = click.style("EXPIRED", fg="red") if p["refresh_expired"] else click.style("valid", fg="green")
        click.echo(click.style(f"\n  ── Patient: {pid}{name_str} ──", bold=True))
        click.echo(f"    Token:        {exp_str}")
        click.echo(f"    Refresh:      {rexp_str}")
        click.echo(f"    Expires in:   {p['seconds_remaining']}s ({p['seconds_remaining'] // 60}m)")
        click.echo(f"    Expires at:   {p['expires_at']}")


def do_refresh(provider: str, patient_id: str | None = None) -> None:
    """Refresh tokens for one or all patients of a provider."""
    mgr = get_auth_manager(provider)
    if patient_id:
        try:
            token = mgr.get_valid_token(force_refresh=True, patient_id=patient_id)
            click.echo(click.style(f"\n[SUCCESS] Token refreshed for patient {patient_id}", bold=True, fg="green"))
            print_token_status(token)
        except RuntimeError as e:
            click.echo(click.style(f"[ERROR] {e}", fg="red"))
            sys.exit(1)
    else:
        pids = mgr.token_store.list_patient_ids()
        if not pids:
            try:
                token = mgr.get_valid_token(force_refresh=True)
                click.echo(click.style("\n[SUCCESS] Token refreshed", bold=True, fg="green"))
                print_token_status(token)
            except RuntimeError as e:
                click.echo(click.style(f"[ERROR] {e}", fg="red"))
                sys.exit(1)
        else:
            for pid in pids:
                try:
                    token = mgr.get_valid_token(force_refresh=True, patient_id=pid)
                    click.echo(click.style(f"\n[SUCCESS] Token refreshed for patient {pid}", bold=True, fg="green"))
                    print_token_status(token)
                except RuntimeError as e:
                    click.echo(click.style(f"[ERROR] Patient {pid}: {e}", fg="red"))


def do_clear(provider: str, patient_id: str | None = None) -> None:
    """Clear stored tokens for one or all patients of a provider."""
    mgr = get_auth_manager(provider)
    if patient_id:
        mgr.clear(patient_id=patient_id)
        click.echo(click.style(f"Token cleared for patient {patient_id}.", fg="yellow"))
    else:
        mgr.clear()
        click.echo(click.style(f"All {provider.upper()} tokens cleared.", fg="yellow"))


def do_daemon(provider: str, interval: int, threshold: int) -> None:
    """Start the background token refresh daemon for a provider."""
    click.echo(
        click.style(
            f"Token refresh daemon started for {provider.upper()} (interval={interval}s, threshold={threshold}s)",
            bold=True,
            fg="green",
        )
    )
    click.echo("Press Ctrl+C to stop.\n")
    daemon = RefreshDaemon(provider=provider, interval=interval, threshold=threshold)
    daemon.run()


def do_auth(provider: str, patient_id: str | None = None) -> None:
    """Ensure a valid token exists, prompting re-auth interactively if not."""
    mgr = get_auth_manager(provider)
    try:
        mgr.get_valid_token(patient_id=patient_id)
    except RuntimeError as e:
        click.echo(click.style(f"\n[ERROR] {e}", fg="red"))
        if click.confirm(click.style("Re-authenticate now?", fg="yellow"), default=True):
            if do_login(provider):
                return
            click.echo(click.style("Re-authentication failed.", fg="red"))
        sys.exit(1)


# ── Print helpers ───────────────────────────────────────────────


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
            import tqdm
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

@click.group(invoke_without_command=True)
@click.pass_context
def anthem_cli(ctx):
    """Anthem/Elevance Health CLI — insurance data, EOB, coverage."""
    ctx.ensure_object(dict)
    ctx.obj["provider"] = "anthem"
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())

# Copy all anthem-provider commands to top level (auth, fhir, eob, patients, etc.)
for _name, _cmd in list(anthem.commands.items()):
    anthem_cli.add_command(_cmd)

# Copy shared top-level commands (provider-agnostic)
for _name in ("mcd", "formulary", "job", "server", "search-claims", "search-eob"):
    anthem_cli.add_command(main.commands[_name])


def anthem_main():
    """Entry point for the ``anthem`` binary. Provider subcommand not needed."""
    anthem_cli.main()


if __name__ == "__main__":
    main()

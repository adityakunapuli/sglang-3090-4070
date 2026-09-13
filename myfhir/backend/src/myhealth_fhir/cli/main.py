"""Click CLI for the Anthem/Elevance Health TotalView FHIR API."""

import json
import os

import click
from tqdm import tqdm

from myhealth_fhir.cli.auth import do_auth, make_auth_group
from myhealth_fhir.fhir.client import get_fhir_client


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

    stats = backfill_anthem(dry_run=dry_run) if only == "anthem" else backfill_ucla(dry_run=dry_run)
    click.echo(f"{only}: {json.dumps(stats)}")


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


# ── Global unauthenticated commands ────────────────────────────


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
    from myhealth_fhir.job.runner import job_loop, run_job_once

    providers = list_providers() if not providers else list(providers)

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

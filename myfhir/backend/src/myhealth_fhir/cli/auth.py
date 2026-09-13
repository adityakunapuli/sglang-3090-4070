"""Auth group factory + auth action helpers (do_login, do_status, do_refresh, ...)."""

import os
import secrets
import sys
import webbrowser

import click
import httpx
from tqdm import tqdm

from myhealth_fhir.cli.output import extract_code, print_token_status
from myhealth_fhir.config.settings import resolve_provider
from myhealth_fhir.fhir.client import get_fhir_client
from myhealth_fhir.services.auth import RefreshDaemon, get_auth_manager
import contextlib

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
        from sqlalchemy import text

        from myhealth_fhir.db.engine import get_anthem_session

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
        with contextlib.suppress(Exception):
            webbrowser.open(authorize_url)
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

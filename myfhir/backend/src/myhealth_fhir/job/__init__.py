"""Job orchestrator: keep tokens alive and pull data for all configured providers.

Used by ``myhealth job`` (one-shot or ``--daemon``) and by the FastAPI app's
lifespan (``start_job_thread``). Writes a per-run ``JobRun`` row to the auth DB
so the dashboard can surface last-run status and totals.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, datetime

from myhealth_fhir.config.settings import list_providers
from myhealth_fhir.db import get_auth_session, get_session_for
from myhealth_fhir.db.models_auth import JobRun
from myhealth_fhir.services.auth import RefreshDaemon, get_auth_manager
from myhealth_fhir.services.fhir_client import get_fhir_client

log = logging.getLogger("myhealth_fhir.job")

# Table counters per provider database, used to report totals + deltas.
_COUNTERS = {
    "anthem": ["eob", "claim_submission"],
    "ucla": [
        "encounter",
        "diagnostic_report",
        "lab_result",
        "imaging_observation",
        "clinical_observation",
        "clinical_note",
        "medication_administration",
        "service_request",
        "specimen",
        "communication",
        "care_team",
    ],
}

# Result-metric keys that represent real records fed to the run summary.
# Everything else (saved/new/updated/db_total/sec/status/error/...) is
# bookkeeping and excluded so the "fetched" totals aren't inflated.
_SUMMARY_METRIC_KEYS = {"count", "panels", "results", "imaging"}


def _count_rows(provider: str) -> dict[str, int]:
    """Return current row counts for a provider's tables."""
    counts: dict[str, int] = {}
    try:
        with get_session_for(provider) as session:
            for table in _COUNTERS.get(provider, []):
                from sqlalchemy import text

                counts[table] = session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0
    except Exception as e:  # table may not exist yet on a fresh DB
        log.debug("Row count for %s failed: %s", provider, e)
    return counts


def _refresh_all_tokens(provider: str) -> dict:
    """Force-refresh tokens for every stored patient of a provider.

    A patient whose refresh token is rejected/expired is recorded as needing
    re-authentication (surfaced by the dashboard) rather than failing the run.
    """
    manager = get_auth_manager(provider)
    pids = manager.token_store.list_all_patient_ids() or ["_default"]
    refreshed: dict[str, str] = {}
    for pid in pids:
        try:
            manager.get_valid_token(force_refresh=True, patient_id=pid)
            refreshed[pid] = "ok"
        except RuntimeError as e:
            refreshed[pid] = f"reauth-neededs: {e}"
            log.warning("Provider %s patient %s: %s", provider, pid, e)
    return refreshed


def _refresh_near_expiry(provider: str) -> None:
    """Best-effort proactive refresh: refresh tokens nearing expiry (no force)."""
    manager = get_auth_manager(provider)
    for pid in manager.token_store.list_all_patient_ids() or ["_default"]:
        try:
            manager.get_valid_token(patient_id=pid)
        except RuntimeError:
            continue


def _job_run_summary(provider: str, results: dict) -> dict:
    """Merge per-patient fetch results into a flat ``fetched`` counts blob.

    Handles the result shapes returned by the fetch methods:
    - EOB/Claims:    {pid: {"count": N, "db_total": N, "error": ...}}
    - Labs:          {pid: {"panels": N, "results": N, "imaging": N, "error": ...}}
    - All-clinical:  {pid: {resource_type: {"new": N, "total": N, ...}}}
    """
    fetched: dict = {}
    for pid, per_patient in results.items():
        if pid in ("claims", "clinical") and isinstance(per_patient, dict):
            # Nested group (claims/clinical reports) — recurse once.
            subgroup = _job_run_summary(provider, per_patient)
            for key, value in subgroup["fetched"].items():
                fetched[key] = fetched.get(key, 0) + value
            continue
        if not isinstance(per_patient, dict):
            continue
        for key, value in per_patient.items():
            if key in ("error", "status"):
                continue
            if isinstance(value, dict):  # resource_type groups
                for rt, sub in value.items():
                    if isinstance(sub, dict):
                        n = int(sub.get("new") or 0)
                        fetched.setdefault(rt, 0)
                        fetched[rt] += n
            elif isinstance(value, (int, float)) and key in _SUMMARY_METRIC_KEYS:
                # Only real record metrics feed the summary:
                #   "count" (EOB/claims saved this run) and the lab counters
                #   (panels/results/imaging). Derived per-patient bookkeeping
                #   (saved/new/updated/db_total/sec/...) is not summed.
                fetched.setdefault(key, 0)
                fetched[key] += int(value or 0)
    return {"fetched": fetched}


def run_provider(provider: str, skip_labs: bool = False) -> dict:
    """Run a single provider pull. Returns job-run summary dict."""
    started = datetime.now(UTC)

    try:
        with get_auth_session() as session:
            run = JobRun(provider=provider, started_at=started, status="running")
            session.add(run)
            session.commit()
            run_id = run.id
    except Exception as e:
        log.warning("Could not persist job_run start for %s: %s", provider, e)
        run_id = None

    totals_before = _count_rows(provider)
    status = "success"
    error: str | None = None
    results: dict = {}
    claims: dict = {}
    clinical: dict = {}

    try:
        from myhealth_fhir.config.settings import resolve_provider

        config = resolve_provider(provider)
        client = get_fhir_client(provider)

        if config.kind == "anthem":
            results = client.fetch_and_store_eobs_all_patients()
            claims = client.fetch_and_store_claims_all_patients()
        else:
            results = client.fetch_and_store_labs_all_patients()
            if not skip_labs:
                clinical = client.fetch_and_store_all_clinical_data(skip_labs=True)
    except Exception as e:
        status = "failed"
        error = str(e)
        log.exception("Provider %s run failed: %s", provider, e)

    totals_after = _count_rows(provider)
    summary: dict = {"status": status, "totals_before": totals_before, "totals_after": totals_after}
    if claims:
        summary["claims"] = _job_run_summary(provider, claims)["fetched"]
    if clinical:
        summary["clinical"] = _job_run_summary(provider, clinical)["fetched"]
    else:
        summary["fetched"] = _job_run_summary(provider, results)["fetched"]
    if error:
        summary["error"] = error
    # New records = total delta across the provider's tracked tables.
    summary["new_total"] = sum(totals_after.values()) - sum(totals_before.values())

    if run_id is not None:
        try:
            with get_auth_session() as session:
                row = session.get(JobRun, run_id)
                if row is not None:
                    row.finished_at = datetime.now(UTC)
                    row.status = status
                    row.counts = summary
                    row.error = error
                    session.commit()
        except Exception as e:
            log.warning("Could not persist job_run finish for %s: %s", provider, e)

    return {"provider": provider, "status": status, "counts": summary}


def run_all(providers: list[str] | None = None, skip_labs: bool = False) -> dict:
    """Run the data pull for all (or given) configured providers."""
    providers = providers or list_providers()
    results: dict[str, dict] = {}
    for provider in providers:
        log.info("Running data pull for provider '%s'", provider)
        results[provider] = run_provider(provider, skip_labs=skip_labs)
    return results


def run_job_once(providers: list[str] | None = None, skip_labs: bool = False) -> dict:
    """Run one data-pull round; the dedicated refresh threads keep tokens alive."""
    return run_all(providers=providers, skip_labs=skip_labs)


def job_loop(interval_minutes: int = 360, providers: list[str] | None = None) -> None:
    """Run the job daemon loop until interrupted.

    Runs one pull immediately, then sleeps ``interval_minutes`` between rounds.
    Token keep-alive is handled by the ``RefreshDaemon`` thread started here.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    log = logging.getLogger("myhealth_fhir.job_daemon")
    providers = providers or list_providers()
    log.info(
        "Job daemon started for %s (interval=%dm)",
        ", ".join(providers),
        interval_minutes,
    )

    # Start a refresh daemon thread per provider to keep access tokens valid.
    refresh_threads: list[threading.Thread] = []
    for provider in providers:
        daemon = RefreshDaemon(provider=provider, interval=300, threshold=600)
        t = threading.Thread(target=daemon.run, name=f"refresh-{provider}", daemon=True)
        t.start()
        refresh_threads.append(t)

    while True:
        try:
            run_job_once(providers=providers)
            log.info("Pull round complete. Sleeping %dm", interval_minutes)
            time.sleep(interval_minutes * 60)
        except KeyboardInterrupt:
            log.info("Job daemon stopped by user")
            break
        except Exception as e:
            log.exception("Unexpected error in job loop: %s", e)
            time.sleep(60)


def start_job_thread(interval_minutes: int = 360, providers: list[str] | None = None) -> threading.Thread:
    """Start the job daemon in a background thread (used by FastAPI lifespan)."""
    t = threading.Thread(
        target=job_loop,
        kwargs={"interval_minutes": interval_minutes, "providers": providers},
        name="myhealth-job-daemon",
        daemon=True,
    )
    t.start()
    return t

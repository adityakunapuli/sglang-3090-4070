"""Job orchestrator: keep tokens alive and pull data for all configured providers.

Used by ``myhealth job`` (one-shot or ``--daemon``) and by the FastAPI app's
lifespan (``start_job_thread``). Writes a per-run ``JobRun`` row to the auth DB
so the dashboard can surface last-run status and totals.
"""


import logging
import threading
import time
from datetime import UTC, datetime

from myhealth_fhir.config.settings import list_providers
from myhealth_fhir.db import get_auth_session, get_session_for
from myhealth_fhir.fhir.client import get_fhir_client
from myhealth_fhir.models.auth import JobRun
from myhealth_fhir.services.auth import RefreshDaemon, get_auth_manager
from myhealth_fhir.services.progress import registry

log = logging.getLogger("myhealth_fhir.job")

# Table counters per provider database, used to report totals + deltas.
COUNTERS = {
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

# Layer 3 of the sync-hardening fix: at most this often, ignore incremental
# checkpoints entirely and pull every EOB/claim from the API. Catches
# anything the incremental windows ever missed and reconciles the whole
# claim history against stored EOBs.
FULL_RESYNC_DAYS = 7

# Concurrency guard: at most one active data-pull per provider at a time,
# whether triggered by the scheduler or an on-demand (dashboard) request.
_ACTIVE_RUNS: set[str] = set()
_ACTIVE_LOCK = threading.Lock()


def is_provider_running(provider: str) -> bool:
    """Return True if a data-pull is currently in progress for the provider."""
    with _ACTIVE_LOCK:
        return provider in _ACTIVE_RUNS


def try_begin_run(provider: str) -> bool:
    """Atomically mark a provider run as in-progress; False if one is already active."""
    with _ACTIVE_LOCK:
        if provider in _ACTIVE_RUNS:
            return False
        _ACTIVE_RUNS.add(provider)
        return True


def end_run(provider: str) -> None:
    """Mark a provider run as finished."""
    with _ACTIVE_LOCK:
        _ACTIVE_RUNS.discard(provider)


def _count_rows(provider: str) -> dict[str, int]:
    """Return current row counts for a provider's tables."""
    counts: dict[str, int] = {}
    try:
        with get_session_for(provider) as session:
            for table in COUNTERS.get(provider, []):
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


def _last_full_resync(provider: str) -> datetime | None:
    """When the provider last ran a checkpoint-blind full resync.

    Full resyncs are flagged via ``counts.full_resync`` on their JobRun row,
    so the decision survives daemon/app restarts.
    """
    try:
        with get_auth_session() as session:
            rows = (
                session.query(JobRun)
                .filter(JobRun.provider == provider)
                .order_by(JobRun.started_at.desc())
                .limit(500)
                .all()
            )
        for row in rows:
            counts = row.counts or {}
            if isinstance(counts, dict) and counts.get("full_resync"):
                ts = row.started_at
                return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts
    except Exception as e:
        log.warning("Could not read last full-resync time for %s: %s", provider, e)
    return None


def _should_full_resync(provider: str) -> bool:
    """True when no full resync is on record, or the last one is a week old."""
    last = _last_full_resync(provider)
    return last is None or (datetime.now(UTC) - last).days >= FULL_RESYNC_DAYS


def run_provider(provider: str, skip_labs: bool = False) -> dict:
    """Run a single provider pull, guarded so only one run per provider is active.

    If a run for this provider is already in progress (scheduler or on-demand),
    the duplicate is skipped rather than run concurrently.
    """
    if not try_begin_run(provider):
        log.warning("Provider %s already has an active run; skipping duplicate", provider)
        return {"provider": provider, "status": "skipped", "counts": {}}
    try:
        return _run_provider_impl(provider, skip_labs)
    finally:
        end_run(provider)


def _run_provider_impl(provider: str, skip_labs: bool = False) -> dict:
    """Run a single provider pull. Returns job-run summary dict."""
    started = datetime.now(UTC)
    registry.reset(provider, stage="starting", message="Starting sync")

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
    full_resync = False
    recon: dict | None = None

    try:
        from myhealth_fhir.config.settings import resolve_provider

        config = resolve_provider(provider)
        client = get_fhir_client(provider)

        if config.kind == "anthem":
            full_resync = _should_full_resync(provider)
            if full_resync:
                log.info(
                    "Provider %s: checkpoint-blind full resync (weekly safety net)",
                    provider,
                )
            results = client.fetch_and_store_eobs_all_patients(
                auto_incremental=not full_resync
            )
            claims = client.fetch_and_store_claims_all_patients(
                auto_incremental=not full_resync
            )
            # Layer 4: after claims land, flag adjudicated claims that still
            # have no matching EOB row (e.g. 20262502A2197-class gaps).
            try:
                recon = client.reconcile_missing_eobs(full=full_resync)
            except Exception:
                log.exception("EOB reconciliation failed for %s", provider)
                recon = None
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
    summary["full_resync"] = full_resync
    if recon is not None and recon.get("missing_count"):
        summary["reconciliation"] = {
            "missing_eob_claims": recon["missing_count"],
            "window": recon.get("window"),
            "truncated": recon.get("truncated", False),
            "claims": recon.get("missing", [])[:25],
        }

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

    registry.finish(
        provider,
        status="done" if status == "success" else "failed",
        message=error or ("Sync complete" if status == "success" else "Sync failed"),
    )
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

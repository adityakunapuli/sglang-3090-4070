"""FHIR client for Anthem/Elevance Health TotalView API."""


import json
import logging
from datetime import UTC, datetime, timedelta

from typing import Any

from fhirpy import SyncFHIRClient
from fhirpy.base.exceptions import MultipleResourcesFound, ResourceNotFound

from myhealth_fhir.fhir.parsing import (
    _cd,
    _clean_error_message,
    _coding_first_code,
    _existing_encounter_id,
    _extract_observation_value,
    _extract_patient_id,
    _fh_error_text,
    _max_last_updated,
    _parse_code_display,
    _parse_dt,
    _parse_loinc,
    client_ref,
)

# Incremental-sync safety margins (sync-hardening fix, layers 1+2):
#   EOB_OVERLAP_HOURS       — each incremental query reaches back this far
#                             before the checkpoint, so EOBs/claims updated
#                             during or after the previous run are re-fetched
#                             instead of falling into a gap. Upserts make the
#                             overlap idempotent and cheap.
#   CHECKPOINT_MARGIN_HOURS — after a successful fetch the checkpoint advances
#                             to max(_lastUpdated seen) minus this margin,
#                             never to "now".
EOB_OVERLAP_HOURS = 72
CHECKPOINT_MARGIN_HOURS = 1

__all__ = [
    "FHIRClient",
    "ResourceNotFound",
    "MultipleResourcesFound",
]

log = logging.getLogger("myhealth_fhir.fhir_client")


def get_fhir_client(provider: str = "anthem") -> "FHIRClient":
    """Return a cached FHIRClient for the given provider name."""
    from myhealth_fhir.config.settings import resolve_provider

    if provider not in FHIRClient.instances:
        config = resolve_provider(provider)
        FHIRClient.instances[provider] = FHIRClient(config)
    return FHIRClient.instances[provider]


class FHIRClient:
    """Synchronous FHIR client wrapping fhirpy with OAuth2 token management."""

    instances: "dict[str, FHIRClient]" = {}

    def __init__(self, config=None):
        """Initialize the client with a ProviderConfig (defaults to anthem)."""
        from myhealth_fhir.config.settings import resolve_provider

        self.config = config or resolve_provider("anthem")
        self.base_url = self.config.fhir_base_url.rstrip("/")
        self.client_obj: SyncFHIRClient | None = None
        self.max_retries = 1

    # ── internal ────────────────────────────────────────────────────────

    def get_auth_manager(self):
        """Return the AuthManager for this client's provider."""
        from myhealth_fhir.services.auth import get_auth_manager

        return get_auth_manager(self.config.name)

    def _report_stage(self, stage: str, total: int = 0, message: str = "") -> None:
        """Report a new sync stage (best-effort; no-ops if no listener is present)."""
        try:
            from myhealth_fhir.services.progress import registry

            registry.set_stage(self.config.name, stage, total=total, message=message)
        except Exception:
            pass

    def _report_patient(self, pid: str, index: int, message: str | None = None) -> None:
        """Advance per-patient progress (best-effort; no-ops if no listener is present)."""
        try:
            from myhealth_fhir.services.progress import registry

            registry.advance(self.config.name, pid, index, message=message)
        except Exception:
            pass

    def get_client_obj(self, patient_id: str | None = None) -> SyncFHIRClient:
        """Build (and cache) a fhirpy SyncFHIRClient with a valid Bearer token."""
        auth_mgr = self.get_auth_manager()
        token = auth_mgr.get_valid_token(patient_id=patient_id)
        token_type = token.token_type.capitalize() if token.token_type else "Bearer"
        self.client_obj = SyncFHIRClient(
            self.base_url,
            authorization=f"{token_type} {token.access_token}",
            extra_headers={
                "Accept": "application/fhir+json",
                "Prefer": "handling=lenient",
            },
            requests_config={"timeout": 60},
        )
        return self.client_obj

    @staticmethod
    def _is_auth_error(e: Exception) -> bool:
        """Check if an exception represents an HTTP 401/403 or OAuth token failure."""
        try:
            from fhirpy.base.exceptions import AuthorizationError, ForbiddenError
            if isinstance(e, (AuthorizationError, ForbiddenError)):
                return True
        except ImportError:
            pass

        if type(e).__name__ in ("AuthorizationError", "ForbiddenError"):
            return True

        err = getattr(e, "response", None)
        if err is not None and getattr(err, "status_code", None) in (401, 403):
            return True

        if getattr(e, "status_code", None) in (401, 403):
            return True

        msg = str(e).lower()
        if '"status_code":401' in msg or '"status_code": 401' in msg or "'status_code': 401" in msg:
            return True
        if '"status_code":403' in msg or '"status_code": 403' in msg or "'status_code': 403" in msg:
            return True
        if any(term in msg for term in (
            "invalid token",
            "token is invalid",
            "token has expired",
            "expired token",
            "invalid_token",
            "access token is invalid",
            "401 unauthorized",
            "401 client error",
        )):
            return True

        return False

    def with_retry(self, func, patient_id: str | None = None):
        """Run func, retrying once after a forced token refresh on HTTP 401/403."""
        auth_mgr = self.get_auth_manager()
        try:
            return func()
        except Exception as e:
            if self._is_auth_error(e) and self.max_retries > 0:
                log.warning("FHIR auth error (%s) — forced token refresh for patient %s, retrying once…", e, patient_id or "default")
                try:
                    auth_mgr.get_valid_token(force_refresh=True, patient_id=patient_id)
                except Exception as refresh_err:
                    log.warning("Token refresh failed during with_retry for patient %s: %s", patient_id or "default", refresh_err)
                    raise
                self.client_obj = None
                return func()
            raise

    # ── public helpers ──────────────────────────────────────────────────

    def client(self) -> SyncFHIRClient:
        """Return a fhirpy client for the primary (default) token."""
        return self.get_client_obj()

    def meta(self) -> dict:
        """Fetch the FHIR server capability statement."""
        c = self.get_client_obj()
        return c.execute("metadata", method="get")

    # -- generic resource operations ------------------------------------

    def search(
        self,
        resource_type: str,
        params: dict | None = None,
        patient_id: str | None = None,
    ) -> dict:
        """Search a FHIR resource type with optional params, returning the raw Bundle."""
        def _search():
            c = self.get_client_obj(patient_id=patient_id)
            ss = c.resources(resource_type)
            if params:
                for k, v in params.items():
                    ss = ss.search(**{k: v})
            return ss.fetch_raw()
        return self.with_retry(_search, patient_id=patient_id)

    def search_all(
        self,
        resource_type: str,
        params: dict | None = None,
        patient_id: str | None = None,
    ) -> list[dict]:
        """Search a FHIR resource type, paginating through all results."""
        def _search_all():
            c = self.get_client_obj(patient_id=patient_id)
            ss = c.resources(resource_type)
            if params:
                for k, v in params.items():
                    ss = ss.search(**{k: v})
            return ss.fetch_all()
        resources = self.with_retry(_search_all, patient_id=patient_id)
        return [r.serialize() for r in resources]

    def get(self, resource_type: str, resource_id: str, patient_id: str | None = None) -> dict:
        """Fetch a single FHIR resource by id."""
        def _get():
            c = self.get_client_obj(patient_id=patient_id)
            return client_ref(resource_type, resource_id, c).to_resource()
        res = self.with_retry(_get, patient_id=patient_id)
        return res.serialize()

    def reference(self, resource_type: str, resource_id: str):
        """Return a fhirpy Reference for the given resource type and id."""
        return self.get_client_obj().reference(resource_type, resource_id)

    # -- convenience: Patient -------------------------------------------

    def list_patients(
        self,
        name: str | None = None,
        birthdate: str | None = None,
        gender: str | None = None,
        count: int = 20,
        patient_id: str | None = None,
    ) -> dict:
        """List Patient resources, scoped to the default or specified patient."""
        kwargs: dict = {"_count": count}
        if name:
            kwargs["name"] = name
        if birthdate:
            kwargs["birthdate"] = birthdate
        if gender:
            kwargs["gender"] = gender

        auth_mgr = self.get_auth_manager()
        token = auth_mgr.token_store.load(patient_id=patient_id)
        if token and token.patient_id:
            kwargs["_id"] = token.patient_id
            kwargs.pop("_count", None)
        elif patient_id:
            kwargs["_id"] = patient_id
            kwargs.pop("_count", None)

        res = self.search("Patient", kwargs, patient_id=patient_id)
        if res.get("resourceType") == "Patient":
            res = {"resourceType": "Bundle", "total": 1, "entry": [{"resource": res}]}
        return res

    def get_patient(self, patient_id: str) -> dict:
        """Fetch a Patient resource by id."""
        return self.get("Patient", patient_id, patient_id=patient_id)

    def resolve_patient_name(self, patient_id: str) -> str:
        """Look up a patient and return their display name."""
        patient = self.get_patient(patient_id)
        return self.extract_patient_name(patient)

    def _cache_patient_identity(self, patient_id: str) -> None:
        """Fetch and persist an Anthem patient identity when the registry lacks it."""
        from myhealth_fhir.db import get_anthem_session
        from myhealth_fhir.db.identity import upsert_patient_name
        from myhealth_fhir.models.anthem import PatientRecord

        try:
            patient = self.get_patient(patient_id)
            name = self.extract_patient_name(patient)
            if not name or name == patient_id:
                return
            with get_anthem_session() as session:
                ref = upsert_patient_name(session, provider="anthem", patient_id=patient_id, name=name)
                record = session.get(PatientRecord, (patient_id, "anthem"))
                if record is None:
                    record = PatientRecord(patient_id=patient_id, provider="anthem")
                record.entity_ref = ref
                session.merge(record)
                session.commit()
        except Exception:
            log.debug("Unable to cache patient identity", exc_info=True)

    @staticmethod
    def extract_patient_name(patient: dict) -> str:
        """Extract a human-readable name from a FHIR Patient resource."""
        names = patient.get("name", [])
        if names:
            official = next((n for n in names if n.get("use") == "official"), names[0])
            given = " ".join(official.get("given", []))
            family = official.get("family", "")
            return f"{given} {family}".strip()
        return patient.get("id", "")

    @staticmethod
    def extract_entity_name(resource: dict) -> str:
        """Extract a human-readable name from a Practitioner/Organization resource.

        Practitioner.name is a list of HumanName objects (given/family).
        Organization.name is a plain string.
        """
        name = resource.get("name")
        if isinstance(name, str):
            return name
        if isinstance(name, list) and name:
            official = next((n for n in name if isinstance(n, dict) and n.get("use") == "official"), name[0])
            if isinstance(official, dict):
                given = " ".join(official.get("given", []))
                family = official.get("family", "")
                return f"{given} {family}".strip()
            return str(official)
        return resource.get("id", "")

    def resolve_entity_name(self, entity_id: str) -> str | None:
        """Resolve a Practitioner/Organization id to a cached or freshly fetched name."""
        from myhealth_fhir.db import get_anthem_session
        from myhealth_fhir.models.anthem import EntityName

        for etype in ("Practitioner", "Organization"):
            try:
                resource = self.get(etype, entity_id)
                if resource and resource.get("resourceType") == etype:
                    name = self.extract_entity_name(resource)
                    npi = None
                    for ident in resource.get("identifier", []):
                        if ident.get("system", "").endswith("/npi"):
                            npi = ident.get("value")
                            break
                    with get_anthem_session() as session:
                        en = EntityName(
                            entity_ref=f"anthem:{etype}:{entity_id}",
                            provider="anthem",
                            entity_type=etype,
                            entity_id=entity_id,
                            name=name, npi=npi,
                        )
                        session.merge(en)
                        session.commit()
                    return name
            except Exception:
                continue
        return None

    def resolve_and_cache_entity_names(self, entity_ids: set[str]) -> dict[str, str]:
        """Resolve many entity ids to names in parallel, caching each in the DB."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        resolved: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=10) as pool:
            fut = {pool.submit(self.resolve_entity_name, eid): eid for eid in entity_ids}
            for f in as_completed(fut):
                eid = fut[f]
                try:
                    name = f.result()
                    if name:
                        resolved[eid] = name
                except Exception:
                    continue
        return resolved

    def collect_and_resolve_eob_entities(self, eob_list: list[dict]) -> dict[str, str]:
        """Collect all provider/patient refs from a list of EOBs and resolve their names."""
        refs: set[str] = set()
        for rec in eob_list:
            pat = rec.get("patient", {})
            if isinstance(pat, dict) and pat.get("reference"):
                refs.add(self.strip_prefix(pat["reference"]))
            prov = rec.get("provider", {})
            if isinstance(prov, dict) and prov.get("reference"):
                refs.add(self.strip_prefix(prov["reference"]))
            payee = rec.get("payee", {}).get("party", {})
            if isinstance(payee, dict) and payee.get("reference"):
                refs.add(self.strip_prefix(payee["reference"]))
            for ct in rec.get("careTeam", []):
                cp = ct.get("provider", {})
                if isinstance(cp, dict) and cp.get("reference"):
                    refs.add(self.strip_prefix(cp["reference"]))
        return self.resolve_and_cache_entity_names(refs)

    def collect_and_resolve_claim_entities(self, claims_list: list[dict]) -> dict[str, str]:
        """Collect all provider/patient/insurer refs from a list of Claims and resolve their names."""
        refs: set[str] = set()
        for rec in claims_list:
            pat = rec.get("patient", {})
            if isinstance(pat, dict) and pat.get("reference"):
                refs.add(self.strip_prefix(pat["reference"]))
            prov = rec.get("provider", {})
            if isinstance(prov, dict) and prov.get("reference"):
                refs.add(self.strip_prefix(prov["reference"]))
            ins = rec.get("insurer", {})
            if isinstance(ins, dict) and ins.get("reference"):
                refs.add(self.strip_prefix(ins["reference"]))
            for ct in rec.get("careTeam", []):
                cp = ct.get("provider", {})
                if isinstance(cp, dict) and cp.get("reference"):
                    refs.add(self.strip_prefix(cp["reference"]))
        return self.resolve_and_cache_entity_names(refs)

    @staticmethod
    def strip_prefix(ref: str) -> str:
        """Strip the leading 'ResourceType/' prefix from a FHIR reference."""
        if "/" in ref:
            return ref.split("/", 1)[1]
        return ref

    # -- convenience: ExplanationOfBenefit ------------------------------

    def list_explanation_of_benefits(
        self,
        patient_id: str | None = None,
        status: str | None = None,
        use: str | None = None,
        created_date_gte: str | None = None,
        created_date_lte: str | None = None,
        service_date_gte: str | None = None,
        service_date_lte: str | None = None,
        lastupdated_gte: str | None = None,
        lastupdated_lte: str | None = None,
        count: int = 20,
        all_pages: bool = False,
    ) -> dict | list[dict]:
        """List ExplanationOfBenefit resources with optional filters."""
        kwargs: dict = {"_count": count}
        if not patient_id:
            auth_mgr = self.get_auth_manager()
            token = auth_mgr.token_store.load()
            if token and token.patient_id:
                patient_id = token.patient_id
        if patient_id:
            kwargs["patient"] = patient_id
        if status:
            kwargs["status"] = status
        if use:
            kwargs["use"] = use
        since_date = service_date_gte or created_date_gte
        until_date = service_date_lte or created_date_lte
        if since_date or until_date:
            date_vals = []
            if since_date:
                date_vals.append(f"ge{since_date}")
            if until_date:
                date_vals.append(f"le{until_date}")
            kwargs["service-date"] = date_vals if len(date_vals) > 1 else date_vals[0]

        if lastupdated_gte or lastupdated_lte:
            lu_vals = []
            if lastupdated_gte:
                lu_vals.append(f"ge{lastupdated_gte}")
            if lastupdated_lte:
                lu_vals.append(f"le{lastupdated_lte}")
            kwargs["_lastUpdated"] = lu_vals if len(lu_vals) > 1 else lu_vals[0]

        if all_pages:
            kwargs.pop("_count", None)
            return self.search_all("ExplanationOfBenefit", kwargs, patient_id=patient_id)
        return self.search("ExplanationOfBenefit", kwargs, patient_id=patient_id)

    def get_explanation_of_benefit(self, eob_id: str) -> dict:
        """Fetch a single ExplanationOfBenefit by id."""
        return self.get("ExplanationOfBenefit", eob_id)

    def fetch_and_store_eobs_all_patients(
        self,
        patient_ids: list[str] | None = None,
        status: str | None = None,
        use: str | None = None,
        created_date_gte: str | None = None,
        lastupdated_gte: str | None = None,
        no_paginate: bool = False,
        on_auth_failure: Any = None,
        auto_incremental: bool = True,
    ) -> dict:
        """Fetch EOBs for all stored (or given) patient IDs and write to DB immediately.

        If on_auth_failure is given, it's called as on_auth_failure(patient_id) when
        token retrieval fails. The callback should attempt re-auth and return True if
        successful, False to skip.

        Returns summary per patient:
        {patient_id: {"count": N, "saved": N, "new": N, "updated": N, "db_total": N, "error": "..." or None}}.
        """
        auth_mgr = self.get_auth_manager()
        if patient_ids is None:
            patient_ids = auth_mgr.token_store.list_patient_ids()
            if not patient_ids:
                token = auth_mgr.token_store.load()
                if token and token.patient_id:
                    patient_ids = [token.patient_id]

        results: dict = {}
        self._report_stage("EOBs", total=len(patient_ids))
        for _i, pid in enumerate(patient_ids):
            self._report_patient(pid, _i)
            token = None
            try:
                token = auth_mgr.get_valid_token(patient_id=pid)
            except RuntimeError as e:
                if on_auth_failure and on_auth_failure(pid):
                    try:
                        token = auth_mgr.get_valid_token(patient_id=pid)
                    except RuntimeError as e2:
                        results[pid] = {
                            "count": 0,
                            "saved": 0,
                            "new": 0,
                            "updated": 0,
                            "db_total": self._count_eobs(pid),
                            "error": _clean_error_message(e2),
                        }
                        continue
                else:
                    results[pid] = {
                        "count": 0,
                        "saved": 0,
                        "new": 0,
                        "updated": 0,
                        "db_total": self._count_eobs(pid),
                        "error": _clean_error_message(e),
                    }
                    continue

            if token is None:
                results[pid] = {
                    "count": 0,
                    "saved": 0,
                    "new": 0,
                    "updated": 0,
                    "db_total": self._count_eobs(pid),
                    "error": "No token after re-auth",
                }
                continue

            # Per-patient incremental date resolution if not explicitly overridden.
            # Layer 1: rewind the checkpoint by the overlap window so records
            # updated around the previous run are re-fetched, never skipped.
            pid_lastupdated = lastupdated_gte
            if pid_lastupdated is None and auto_incremental:
                last_fetch = auth_mgr.token_store.get_last_eob_fetch(pid)
                if last_fetch:
                    if last_fetch.tzinfo is None:
                        last_fetch = last_fetch.replace(tzinfo=UTC)
                    pid_lastupdated = (
                        last_fetch - timedelta(hours=EOB_OVERLAP_HOURS)
                    ).strftime("%Y-%m-%d")

            try:
                data = self.list_explanation_of_benefits(
                    patient_id=pid,
                    status=status,
                    use=use,
                    # When filtering by _lastUpdated (incremental) don't ALSO
                    # constrain service-date — that would drop recently
                    # re-adjudicated EOBs whose original service date is older
                    # than the checkpoint. Service-date filtering is only used
                    # when the caller explicitly requests a created-date window.
                    created_date_gte=None if pid_lastupdated else created_date_gte,
                    lastupdated_gte=pid_lastupdated,
                    count=100,
                    all_pages=not no_paginate,
                )
                if no_paginate:
                    eobs = [entry.get("resource", {}) for entry in data.get("entry", [])]
                else:
                    eobs = data

                count_before = self._count_eobs(pid)
                saved_count = self.save_eobs_to_db(eobs)
                count_after = self._count_eobs(pid)
                new_count = max(0, count_after - count_before)
                updated_count = max(0, saved_count - new_count)

                results[pid] = {
                    "count": saved_count,
                    "saved": saved_count,
                    "new": new_count,
                    "updated": updated_count,
                    "db_total": count_after,
                    "error": None,
                }
                # Layer 2: advance the checkpoint to the newest _lastUpdated
                # actually seen (minus a safety margin), NOT "now" — "now"
                # would skip anything adjudicated between the query and this
                # write. The 72h overlap covers the margin and any stragglers.
                newest_updated = _max_last_updated(eobs)
                checkpoint = (
                    newest_updated - timedelta(hours=CHECKPOINT_MARGIN_HOURS)
                    if newest_updated is not None
                    else datetime.now(UTC) - timedelta(hours=CHECKPOINT_MARGIN_HOURS)
                )
                auth_mgr.token_store.set_last_eob_fetch(pid, checkpoint)
                log.info(
                    "Stored %d EOBs (%d new, %d updated) for patient %s (total %d)",
                    saved_count,
                    new_count,
                    updated_count,
                    pid,
                    count_after,
                )
                try:
                    self.collect_and_resolve_eob_entities(eobs)
                except Exception:
                    log.exception("Entity name resolution failed for patient %s", pid)
            except Exception as e:
                log.exception("EOB fetch failed for patient %s", pid)
                results[pid] = {
                    "count": 0,
                    "saved": 0,
                    "new": 0,
                    "updated": 0,
                    "db_total": self._count_eobs(pid),
                    "error": _clean_error_message(e),
                }

        return results

    def fetch_and_store_labs_all_patients(
        self,
        patient_ids: list[str] | None = None,
        on_auth_failure: Any = None,
        **params,
    ) -> dict:
        """Fetch labs for all stored (or given) patient IDs and write to DB.

        params: search parameters for DiagnosticReport (category, date, _sort, etc.)
        on_auth_failure: callback(patient_id) -> bool; return True to re-auth, False to skip.

        Returns {patient_id: {"panels": N, "results": N, "imaging": N, "error": str|None}}.
        """
        from myhealth_fhir.services.fhir_client import save_labs_to_db, save_imaging_observations
        import httpx

        auth_mgr = self.get_auth_manager()
        if patient_ids is None:
            patient_ids = auth_mgr.token_store.list_patient_ids()
            if not patient_ids:
                token = auth_mgr.token_store.load()
                if token and token.patient_id:
                    patient_ids = [token.patient_id]

        results: dict = {}
        self._report_stage("Labs", total=len(patient_ids))
        for _i, pid in enumerate(patient_ids):
            self._report_patient(pid, _i)
            try:
                token = auth_mgr.get_valid_token(patient_id=pid)
            except RuntimeError as e:
                if on_auth_failure and on_auth_failure(pid):
                    try:
                        token = auth_mgr.get_valid_token(patient_id=pid)
                    except RuntimeError as e2:
                        results[pid] = {"panels": 0, "results": 0, "imaging": 0, "error": f"Re-auth failed: {e2}"}
                        continue
                else:
                    results[pid] = {"panels": 0, "results": 0, "imaging": 0, "error": f"Auth failed: {e}"}
                    continue

            if token is None:
                results[pid] = {"panels": 0, "results": 0, "imaging": 0, "error": "No token after re-auth"}
                continue

            headers = {
                "Authorization": f"Bearer {token.access_token}",
                "Accept": "application/fhir+json",
            }

            # ── Fetch DiagnosticReports ──
            search_params = dict(params)
            search_params.setdefault("_sort", "-date")
            search_params.setdefault("_count", 100)
            query_parts = "&".join(
                f"{k}={v}" if isinstance(v, str)
                else f"{k}={'&'.join(str(x) for x in v) if isinstance(v, (list, tuple)) else v}"
                for k, v in search_params.items()
            )
            dr_url = f"{self.base_url}/DiagnosticReport?{query_parts}&patient={pid}"

            all_reports = []
            try:
                resp = httpx.get(dr_url, headers=headers, timeout=120)
                resp.raise_for_status()
                bundle = resp.json()
                all_reports.extend(entry.get("resource", {}) for entry in bundle.get("entry", []))
                while True:
                    links = bundle.get("link", [])
                    next_link = next((l for l in links if l.get("relation") == "next"), None)
                    if not next_link:
                        break
                    url = next_link.get("url", "")
                    if not url:
                        break
                    resp2 = httpx.get(url, headers=headers, timeout=120)
                    resp2.raise_for_status()
                    bundle = resp2.json()
                    all_reports.extend(entry.get("resource", {}) for entry in bundle.get("entry", []))
            except Exception as e:
                results[pid] = {"panels": 0, "results": 0, "imaging": 0, "error": f"DiagnosticReport fetch failed: {e}"}
                continue

            # ── Save DiagnosticReports to DB ──
            panel_count, result_count = 0, 0
            try:
                sp, sr, _ = save_labs_to_db(self, all_reports, provider=self.config.name)
                panel_count, result_count = sp, sr
            except Exception as e:
                results[pid] = {"panels": 0, "results": 0, "imaging": 0, "error": f"DB save failed: {e}"}
                continue

            # ── Fetch standalone ImagingObservations ──
            img_obs_count = 0
            try:
                img_url = f"{self.base_url}/Observation?category=imaging&_sort=-date&_count=100&patient={pid}"
                resp_img = httpx.get(img_url, headers=headers, timeout=120)
                resp_img.raise_for_status()
                bundle_img = resp_img.json()
                all_img_obs = [entry.get("resource", {}) for entry in bundle_img.get("entry", [])]
                while True:
                    links_img = bundle_img.get("link", [])
                    next_img = next((l for l in links_img if l.get("relation") == "next"), None)
                    if not next_img:
                        break
                    url_img = next_img.get("url", "")
                    if not url_img:
                        break
                    resp3 = httpx.get(url_img, headers=headers, timeout=120)
                    resp3.raise_for_status()
                    bundle_img = resp3.json()
                    all_img_obs.extend(entry.get("resource", {}) for entry in bundle_img.get("entry", []))

                img_obs_count = save_imaging_observations(self, all_img_obs, provider=self.config.name)
            except Exception:
                pass

            results[pid] = {"panels": panel_count, "results": result_count, "imaging": img_obs_count, "error": None}

        return results

    def fetch_and_store_all_clinical_data(
        self,
        patient_ids: list[str] | None = None,
        skip_labs: bool = False,
        on_auth_failure: Any = None,
    ) -> dict:
        """Fetch ALL clinical FHIR resources for each patient and persist to DB.

        Iterates resource types in dependency order (encounters first for FK
        linkage). Returns {patient_id: {resource_type: count, ...}}.
        """
        import httpx
        from myhealth_fhir.db import init_db
        init_db()

        auth_mgr = self.get_auth_manager()
        if patient_ids is None:
            patient_ids = auth_mgr.token_store.list_patient_ids()
            if not patient_ids:
                token = auth_mgr.token_store.load()
                if token and token.patient_id:
                    patient_ids = [token.patient_id]

        resource_types = [
            ("Encounter", {}, save_encounters_to_db),
            ("Condition", {}, save_conditions_to_db),
            ("Procedure", {}, save_procedures_to_db),
            ("MedicationStatement", {}, save_medication_statements_to_db),
            ("MedicationRequest", {}, save_medication_requests_to_db),
            ("MedicationAdministration", {}, save_medication_administrations_to_db),
            ("AllergyIntolerance", {}, save_allergies_to_db),
            ("Immunization", {}, save_immunizations_to_db),
            ("CarePlan", {}, save_care_plans_to_db),
            ("CareTeam", {}, save_care_teams_to_db),
            ("DocumentReference", {}, "docs"),
            ("FamilyMemberHistory", {}, save_family_member_histories_to_db),
            ("Specimen", {}, save_specimens_to_db),
            ("ServiceRequest", {}, save_service_requests_to_db),
            ("Communication", {}, save_communications_to_db),
            ("Observation", {"category": "vital-signs"}, save_clinical_observations_to_db),
        ]
        if not skip_labs:
            resource_types.append(("DiagnosticReport", {"category": "lab"}, "labs"))

        def _fetch_all_pages(rt: str, params: dict, headers: dict) -> tuple[list[dict], str | None]:
            """Fetch all pages for a resource type.

            Returns (resources, error) — error is a short message when the
            search itself failed (permission denied, unsupported type, etc.)
            so callers can distinguish 'unavailable' from a successful empty page.
            """
            search_params = dict(params)
            search_params.setdefault("_count", 100)
            qs = "&".join(
                f"{k}={v}" if isinstance(v, str)
                else f"{k}={'&'.join(str(x) for x in v) if isinstance(v, (list, tuple)) else v}"
                for k, v in search_params.items()
            )
            url = f"{self.base_url}/{rt}?{qs}"
            all_resources = []
            try:
                resp = httpx.get(url, headers=headers, timeout=120)
                resp.raise_for_status()
                bundle = resp.json()
                all_resources.extend(entry.get("resource", {}) for entry in bundle.get("entry", []))
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
                    all_resources.extend(entry.get("resource", {}) for entry in bundle.get("entry", []))
            except httpx.HTTPStatusError as e:
                error = f"{e.response.status_code}: {_fh_error_text(e.response)}"
                return [], error
            except (httpx.HTTPError, ValueError) as e:  # network / malformed JSON
                return [], f"request failed: {e.__class__.__name__}"
            return all_resources, None

        results: dict = {}
        self._report_stage("Clinical", total=len(patient_ids))
        for _i, pid in enumerate(patient_ids):
            self._report_patient(pid, _i)
            try:
                token = auth_mgr.get_valid_token(patient_id=pid)
            except RuntimeError as e:
                if on_auth_failure and on_auth_failure(pid):
                    try:
                        token = auth_mgr.get_valid_token(patient_id=pid)
                    except RuntimeError as e2:
                        results[pid] = {"error": f"Re-auth failed: {e2}"}
                        continue
                else:
                    results[pid] = {"error": f"Auth failed: {e}"}
                    continue
            if token is None:
                results[pid] = {"error": "No token after re-auth"}
                continue

            headers = {
                "Authorization": f"Bearer {token.access_token}",
                "Accept": "application/fhir+json",
            }
            patient_results: dict = {}
            for rt, rt_params, save_fn in resource_types:
                self._report_patient(pid, _i, message=rt)
                rt_params_with_patient = dict(rt_params)
                rt_params_with_patient["patient"] = pid

                import time
                start = time.time()
                resources, fetch_error = _fetch_all_pages(rt, rt_params_with_patient, headers)
                elapsed = time.time() - start

                if fetch_error:
                    patient_results[rt] = {"new": 0, "total": 0, "sec": round(elapsed, 1), "error": fetch_error}
                    continue

                if isinstance(save_fn, str) and save_fn == "labs":
                    from myhealth_fhir.services.fhir_client import save_labs_to_db, save_imaging_observations
                    panels, results_count, _ = save_labs_to_db(self, resources, provider=self.config.name)
                    patient_results["DiagnosticReport"] = {"new": panels, "total": len(resources), "sec": round(elapsed, 1)}
                    img_url = f"{self.base_url}/Observation?category=imaging&_count=100&patient={pid}"
                    try:
                        resp_img = httpx.get(img_url, headers=headers, timeout=120)
                        resp_img.raise_for_status()
                        bundle_img = resp_img.json()
                        img_obs = [e.get("resource", {}) for e in bundle_img.get("entry", [])]
                        while True:
                            links_img = bundle_img.get("link", [])
                            next_img = next((l for l in links_img if l.get("relation") == "next"), None)
                            if not next_img:
                                break
                            url_img = next_img.get("url", "")
                            if not url_img:
                                break
                            resp3 = httpx.get(url_img, headers=headers, timeout=120)
                            resp3.raise_for_status()
                            bundle_img = resp3.json()
                            img_obs.extend(e.get("resource", {}) for e in bundle_img.get("entry", []))
                        img_new = save_imaging_observations(self, img_obs, provider=self.config.name)
                        patient_results["ImagingObservation"] = {"new": img_new, "total": len(img_obs), "sec": 0}
                    except httpx.HTTPStatusError as e:
                        patient_results["ImagingObservation"] = {
                            "new": 0, "total": 0, "sec": 0,
                            "error": f"{e.response.status_code}: {_fh_error_text(e.response)}",
                        }
                    except (httpx.HTTPError, ValueError):
                        patient_results["ImagingObservation"] = {"new": 0, "total": 0, "sec": 0, "error": "request failed"}
                elif isinstance(save_fn, str) and save_fn == "docs":
                    from myhealth_fhir.services.fhir_client import (
                        save_clinical_notes_from_docs,
                        save_document_references_to_db,
                    )
                    doc_new = save_document_references_to_db(resources, provider=self.config.name)
                    note_new = save_clinical_notes_from_docs(self, resources, headers, provider=self.config.name)
                    patient_results["DocumentReference"] = {"new": doc_new, "total": len(resources), "sec": round(elapsed, 1)}
                    patient_results["ClinicalNote"] = {"new": note_new, "total": len(resources), "sec": round(elapsed, 1)}
                else:
                    new_count = save_fn(resources, provider=self.config.name)
                    patient_results[rt] = {"new": new_count, "total": len(resources), "sec": round(elapsed, 1)}

            results[pid] = patient_results
        return results

    def _count_eobs(self, patient_id: str) -> int:
        """Return total EOB row count in the DB for a patient."""
        from myhealth_fhir.db import get_anthem_session
        from myhealth_fhir.models.anthem import EOB
        from myhealth_fhir.db.identity import entity_ref
        from sqlalchemy import func

        with get_anthem_session() as session:
            return session.query(func.count(EOB.id)).filter(
                EOB.patient_ref == entity_ref("anthem", "Patient", patient_id)
            ).scalar() or 0

    def _count_claims(self, patient_id: str) -> int:
        """Return total ClaimSubmission row count in the DB for a patient."""
        from myhealth_fhir.db import get_anthem_session
        from myhealth_fhir.models.anthem import ClaimSubmission
        from myhealth_fhir.db.identity import entity_ref
        from sqlalchemy import func

        with get_anthem_session() as session:
            return session.query(func.count(ClaimSubmission.id)).filter(
                ClaimSubmission.patient_ref == entity_ref("anthem", "Patient", patient_id)
            ).scalar() or 0

    def save_eobs_to_db(self, eobs: list[dict]) -> int:
        """Write EOB dicts to the anthem DB. Handles upserts by delete-and-reinsert."""
        from myhealth_fhir.models.anthem import (
            EOB,
            EOBItem,
            EOBItemAdjudication,
            EOBDiagnosis,
            EOBCareTeam,
            EOBTotal,
            EOBIdentifier,
            EOBAdjudication,
            EOBSupportingInfo,
            EOBProcedure,
        )
        from myhealth_fhir.db import get_anthem_session

        from myhealth_fhir.db.parser import (
            load_eob,
            load_item,
            load_diagnoses,
            load_care_team,
            load_totals,
            load_item_adjudications,
            load_eob_identifiers,
            load_eob_adjudications,
            load_eob_supporting_info,
            load_eob_procedures,
        )

        count = 0
        patient_ids = {
            rec.get("patient", {}).get("reference", "").split("/", 1)[-1]
            for rec in eobs if isinstance(rec.get("patient"), dict) and rec["patient"].get("reference")
        }
        for patient_id in patient_ids:
            self._cache_patient_identity(patient_id)
        with get_anthem_session() as session:
            for rec in eobs:
                eob_id = rec["id"]
                existing = session.get(EOB, eob_id)
                if existing:
                    session.delete(existing)
                    session.flush()
                eob = EOB(**load_eob(rec))
                session.add(eob)
                session.flush()

                for item_rec in rec.get("item", []):
                    item = EOBItem(**load_item(item_rec, eob_id))
                    session.add(item)
                    session.flush()
                    for adj in load_item_adjudications(item_rec, item.id):
                        session.add(EOBItemAdjudication(**adj))

                for dx in load_diagnoses(rec, eob_id):
                    session.add(EOBDiagnosis(**dx))

                for ct in load_care_team(rec, eob_id):
                    session.add(EOBCareTeam(**ct))

                for t in load_totals(rec, eob_id):
                    session.add(EOBTotal(**t))

                for ident in load_eob_identifiers(rec, eob_id):
                    session.add(EOBIdentifier(**ident))

                for adj in load_eob_adjudications(rec, eob_id):
                    session.add(EOBAdjudication(**adj))

                for si in load_eob_supporting_info(rec, eob_id):
                    session.add(EOBSupportingInfo(**si))

                for proc in load_eob_procedures(rec, eob_id):
                    session.add(EOBProcedure(**proc))

                count += 1
                if count % 20 == 0:
                    session.flush()

            session.commit()
        try:
            from myhealth_fhir.services.member_submissions import match_registered_submissions
            matched = match_registered_submissions(eobs=eobs, claims=[])
            if matched:
                log.info("Matched %d registered member submissions to EOBs", matched)
        except Exception:
            log.exception("Member-submission matching failed after EOB save")
        return count

    # -- convenience: Coverage -------------------------------------------

    def list_coverage(
        self,
        patient_id: str | None = None,
        count: int = 20,
    ) -> dict:
        """List Coverage resources for a patient (default to stored patient)."""
        kwargs: dict = {"_count": count}
        if not patient_id:
            auth_mgr = self.get_auth_manager()
            token = auth_mgr.token_store.load()
            if token and token.patient_id:
                patient_id = token.patient_id
        if patient_id:
            kwargs["patient"] = patient_id
        return self.search("Coverage", kwargs, patient_id=patient_id)

    # -- convenience: Claim -----------------------------------------------

    def list_claims(
        self,
        patient_id: str | None = None,
        status: str | None = None,
        use: str | None = None,
        lastupdated_gte: str | None = None,
        lastupdated_lte: str | None = None,
        count: int = 20,
        all_pages: bool = False,
    ) -> dict | list[dict]:
        """List Claim resources for a patient with optional filters."""
        kwargs: dict = {"_count": count}
        if not patient_id:
            auth_mgr = self.get_auth_manager()
            token = auth_mgr.token_store.load()
            if token and token.patient_id:
                patient_id = token.patient_id
        if patient_id:
            kwargs["patient"] = patient_id
        if status:
            kwargs["status"] = status
        if use:
            kwargs["use"] = use
        if lastupdated_gte or lastupdated_lte:
            lu_vals = []
            if lastupdated_gte:
                lu_vals.append(f"ge{lastupdated_gte}")
            if lastupdated_lte:
                lu_vals.append(f"le{lastupdated_lte}")
            kwargs["_lastUpdated"] = lu_vals if len(lu_vals) > 1 else lu_vals[0]
        if all_pages:
            kwargs.pop("_count", None)
            return self.search_all("Claim", kwargs, patient_id=patient_id)
        return self.search("Claim", kwargs, patient_id=patient_id)

    def get_claim(self, claim_id: str, patient_id: str | None = None) -> dict:
        """Fetch a single Claim by id."""
        return self.get("Claim", claim_id, patient_id=patient_id)

    def fetch_and_store_claims_all_patients(
        self,
        patient_ids: list[str] | None = None,
        status: str | None = None,
        use: str | None = None,
        lastupdated_gte: str | None = None,
        on_auth_failure: Any = None,
        auto_incremental: bool = True,
    ) -> dict:
        """Fetch Claim resources for all stored (or given) patient IDs and write to DB.

        Uses ``_lastUpdated`` so changed claims are re-fetched (adjudication,
        status transitions, corrections).  Auto-incremental: on first run
        pulls **all** claims; subsequent runs pull only claims updated since
        the last successful fetch per patient.

        Returns summary per patient:
        {patient_id: {"count": N, "saved": N, "new": N, "updated": N, "db_total": N, "error": "..." or None}}.
        """
        auth_mgr = self.get_auth_manager()
        if patient_ids is None:
            patient_ids = auth_mgr.token_store.list_patient_ids()
            if not patient_ids:
                token = auth_mgr.token_store.load()
                if token and token.patient_id:
                    patient_ids = [token.patient_id]

        results: dict = {}
        self._report_stage("Claims", total=len(patient_ids))
        for _i, pid in enumerate(patient_ids):
            self._report_patient(pid, _i)
            token = None
            try:
                token = auth_mgr.get_valid_token(patient_id=pid)
            except RuntimeError as e:
                if on_auth_failure and on_auth_failure(pid):
                    try:
                        token = auth_mgr.get_valid_token(patient_id=pid)
                    except RuntimeError as e2:
                        results[pid] = {
                            "count": 0,
                            "saved": 0,
                            "new": 0,
                            "updated": 0,
                            "db_total": self._count_claims(pid),
                            "error": _clean_error_message(e2),
                        }
                        continue
                else:
                    results[pid] = {
                        "count": 0,
                        "saved": 0,
                        "new": 0,
                        "updated": 0,
                        "db_total": self._count_claims(pid),
                        "error": _clean_error_message(e),
                    }
                    continue

            if token is None:
                results[pid] = {
                    "count": 0,
                    "saved": 0,
                    "new": 0,
                    "updated": 0,
                    "db_total": self._count_claims(pid),
                    "error": "No token after re-auth",
                }
                continue

            # Per-patient incremental date resolution if not explicitly overridden.
            # Layer 1 (same overlap window as the EOB sync).
            pid_lastupdated = lastupdated_gte
            if pid_lastupdated is None and auto_incremental:
                last_fetch = auth_mgr.token_store.get_last_claim_fetch(pid)
                if last_fetch:
                    if last_fetch.tzinfo is None:
                        last_fetch = last_fetch.replace(tzinfo=UTC)
                    pid_lastupdated = (
                        last_fetch - timedelta(hours=EOB_OVERLAP_HOURS)
                    ).strftime("%Y-%m-%d")

            try:
                data = self.list_claims(
                    patient_id=pid,
                    status=status,
                    use=use,
                    lastupdated_gte=pid_lastupdated,
                    count=100,
                    all_pages=True,
                )
                claims = data

                count_before = self._count_claims(pid)
                saved_count = self.save_claims_to_db(claims)
                count_after = self._count_claims(pid)
                new_count = max(0, count_after - count_before)
                updated_count = max(0, saved_count - new_count)

                results[pid] = {
                    "count": saved_count,
                    "saved": saved_count,
                    "new": new_count,
                    "updated": updated_count,
                    "db_total": count_after,
                    "error": None,
                }
                # Layer 2 (same as EOB sync): checkpoint = newest _lastUpdated
                # seen minus safety margin, never "now".
                newest_updated = _max_last_updated(claims)
                checkpoint = (
                    newest_updated - timedelta(hours=CHECKPOINT_MARGIN_HOURS)
                    if newest_updated is not None
                    else datetime.now(UTC) - timedelta(hours=CHECKPOINT_MARGIN_HOURS)
                )
                auth_mgr.token_store.set_last_claim_fetch(pid, checkpoint)
                log.info(
                    "Stored %d claims (%d new, %d updated) for patient %s (total %d)",
                    saved_count,
                    new_count,
                    updated_count,
                    pid,
                    count_after,
                )
                try:
                    self.collect_and_resolve_claim_entities(claims)
                except Exception:
                    log.exception("Entity name resolution failed for claims patient %s", pid)
            except Exception as e:
                log.exception("Claim fetch failed for patient %s", pid)
                results[pid] = {
                    "count": 0,
                    "saved": 0,
                    "new": 0,
                    "updated": 0,
                    "db_total": self._count_claims(pid),
                    "error": _clean_error_message(e),
                }

        return results

    def reconcile_missing_eobs(self, full: bool = False, limit: int = 100) -> dict:
        """Layer 4: DB-level reconciliation — adjudicated claims with no EOB.

        A claim is considered covered when an ``eob`` row exists with the
        same ``claim_number`` (the same join the vw_claims views use). Only
        claims with ``use='claim'``, ``status='active'`` and a claim_number
        are considered; preauthorizations are legitimately EOB-less.

        Window: unless ``full=True`` (weekly checkpoint-blind resync), only
        claims updated since the earliest per-patient EOB checkpoint minus
        the overlap window are checked — the same window the incremental
        sync just covered. Everything older is swept by the weekly full
        resync.

        Read-only: flags gaps in the job-run summary and logs; it never
        mutates data.
        """
        from myhealth_fhir.db import get_anthem_session
        from myhealth_fhir.models.anthem import ClaimSubmission, EOB

        window_start = None
        if not full:
            auth_mgr = self.get_auth_manager()
            pids = auth_mgr.token_store.list_patient_ids() or []
            checkpoints = [
                cp
                for cp in (
                    auth_mgr.token_store.get_last_eob_fetch(p) for p in pids
                )
                if cp is not None
            ]
            if checkpoints:
                earliest = min(checkpoints)
                if earliest.tzinfo is None:
                    earliest = earliest.replace(tzinfo=UTC)
                window_start = (
                    earliest - timedelta(hours=EOB_OVERLAP_HOURS)
                ).replace(tzinfo=None)

        with get_anthem_session() as session:
            q = (
                session.query(
                    ClaimSubmission.id,
                    ClaimSubmission.claim_number,
                    ClaimSubmission.patient_ref,
                    ClaimSubmission.created_date,
                    ClaimSubmission.total_amount,
                    ClaimSubmission.submission_origin,
                    ClaimSubmission.adjudication_status_code,
                    ClaimSubmission.denial_reason_code,
                    ClaimSubmission.last_updated,
                )
                .outerjoin(EOB, EOB.claim_number == ClaimSubmission.claim_number)
                .filter(
                    ClaimSubmission.use == "claim",
                    ClaimSubmission.status == "active",
                    ClaimSubmission.claim_number.isnot(None),
                    EOB.id.is_(None),
                )
            )
            if window_start is not None:
                q = q.filter(ClaimSubmission.last_updated >= window_start)
            q = q.order_by(ClaimSubmission.last_updated.desc()).limit(limit + 1)
            rows = q.all()

        missing = [
            {
                "claim_id": r.id,
                "claim_number": r.claim_number,
                "patient_ref": r.patient_ref,
                "created_date": r.created_date.isoformat() if r.created_date else None,
                "total_amount": r.total_amount,
                "submission_origin": r.submission_origin,
                "adjudication_status_code": r.adjudication_status_code,
                "denial_reason_code": r.denial_reason_code,
                "last_updated": (
                    r.last_updated.isoformat() if r.last_updated else None
                ),
            }
            for r in rows
        ]
        truncated = len(missing) > limit
        result = {
            "window": "all-time" if full else str(window_start),
            "missing_count": len(missing),
            "truncated": truncated,
            "missing": missing[:limit],
        }
        if missing:
            log.warning(
                "EOB reconciliation: %d active claim(s) have no matching EOB "
                "(window=%s%s)",
                len(missing),
                "all-time" if full else str(window_start),
                ", list truncated" if truncated else "",
            )
            for m in missing[:limit]:
                log.warning(
                    "  missing EOB: claim %s number=%s origin=%s updated=%s",
                    m["claim_id"],
                    m["claim_number"],
                    m["submission_origin"],
                    m["last_updated"],
                )
        return result

    def save_claims_to_db(self, claims: list[dict]) -> int:
        """Write Claim dicts to the anthem DB. Handles upserts by delete-and-reinsert."""
        from myhealth_fhir.models.anthem import (
            ClaimSubmission,
            ClaimItem,
            ClaimDiagnosis,
            ClaimCareTeam,
            ClaimIdentifier,
        )
        from myhealth_fhir.db import get_anthem_session
        from myhealth_fhir.db.parser import (
            load_claim,
            load_claim_item,
            load_claim_diagnoses,
            load_claim_care_team,
            load_claim_identifiers,
        )

        count = 0
        patient_ids = {
            rec.get("patient", {}).get("reference", "").split("/", 1)[-1]
            for rec in claims if isinstance(rec.get("patient"), dict) and rec["patient"].get("reference")
        }
        for patient_id in patient_ids:
            self._cache_patient_identity(patient_id)
        with get_anthem_session() as session:
            for rec in claims:
                claim_id = rec["id"]
                existing = session.get(ClaimSubmission, claim_id)
                if existing:
                    session.delete(existing)
                    session.flush()
                claim = ClaimSubmission(**load_claim(rec))
                session.add(claim)
                session.flush()

                for item_rec in rec.get("item", []):
                    item = ClaimItem(**load_claim_item(item_rec, claim_id))
                    session.add(item)

                for dx in load_claim_diagnoses(rec, claim_id):
                    session.add(ClaimDiagnosis(**dx))

                for ct in load_claim_care_team(rec, claim_id):
                    session.add(ClaimCareTeam(**ct))

                for ident in load_claim_identifiers(rec, claim_id):
                    session.add(ClaimIdentifier(**ident))

                count += 1
                if count % 20 == 0:
                    session.flush()

            session.commit()
        try:
            from myhealth_fhir.services.member_submissions import match_registered_submissions
            matched = match_registered_submissions(eobs=[], claims=claims)
            if matched:
                log.info("Matched %d registered member submissions to claims", matched)
        except Exception:
            log.exception("Member-submission matching failed after claim save")
        return count

    # -- convenience: Organization ----------------------------------------

    def list_organizations(
        self,
        name: str | None = None,
        active: bool | None = None,
        count: int = 20,
    ) -> dict:
        """List Organization resources with optional filters."""
        kwargs: dict = {"_count": count}
        if name:
            kwargs["name"] = name
        if active is not None:
            kwargs["active"] = active
        return self.search("Organization", kwargs)

    def get_organization(self, org_id: str) -> dict:
        """Fetch a single Organization by id."""
        return self.get("Organization", org_id)

    # -- unregistered endpoints (no auth required) ------------------------

    @staticmethod
    def cms_mandate_mcd(lastupdated_gte: str | None = None) -> dict:
        """Query the unregistered CMS Mandate MCD endpoint (no auth required)."""
        import httpx

        url = "https://totalview.healthos.elevancehealth.com/resources/unregistered/api/v1/fhir/cms_mandate/mcd/"
        params: dict = {}
        if lastupdated_gte:
            params["lastupdated"] = f"ge{lastupdated_gte}"
        resp = httpx.get(url, timeout=60)
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {"error": "No JSON response", "status_code": resp.status_code}

    @staticmethod
    def cms_mandate_formulary(
        drug: str | None = None,
        count: int = 20,
    ) -> dict:
        """Query the unregistered CMS Mandate Formulary endpoint (no auth required)."""
        import httpx

        url = "https://totalview.healthos.elevancehealth.com/resources/unregistered/api/v1/fhir/cms_mandate/frmlry"
        params: dict = {"_count": count}
        if drug:
            params["drug"] = drug
        resp = httpx.get(url, timeout=60)
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {"error": "No JSON response", "status_code": resp.status_code}


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
    import json

    from myhealth_fhir.db import get_ucla_session
    from myhealth_fhir.models.ucla import DiagnosticReport, DiagnosticReportIdentifier, LabResult, LabResultComponent
    from myhealth_fhir.db.ucla_unpack import extract_diagnostic_report, extract_lab_result

    new_panels = 0
    new_results = 0
    skipped_obs = 0

    with get_ucla_session() as session:
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
    from myhealth_fhir.db import get_ucla_session
    from myhealth_fhir.models.ucla import ImagingObservation

    with get_ucla_session() as session:
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


def save_encounters_to_db(resources: list[dict], provider: str = "ucla") -> int:
    import json
    from myhealth_fhir.db import get_ucla_session
    from myhealth_fhir.models.ucla import Encounter, EncounterIdentifier, EncounterParticipant
    from myhealth_fhir.db.ucla_unpack import extract_encounter, participant_new_fields

    count = 0
    with get_ucla_session() as session:
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
                count += 1
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
    return count


# ── Condition ──────────────────────────────────────────────────────


def save_conditions_to_db(resources: list[dict], provider: str = "ucla") -> int:
    import json
    from myhealth_fhir.db import get_ucla_session
    from myhealth_fhir.models.ucla import Condition
    from myhealth_fhir.db.ucla_unpack import extract_condition

    count = 0
    with get_ucla_session() as session:
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
                count += 1
        session.commit()
    return count


# ── Procedure ──────────────────────────────────────────────────────


def save_procedures_to_db(resources: list[dict], provider: str = "ucla") -> int:
    import json
    from myhealth_fhir.db import get_ucla_session
    from myhealth_fhir.models.ucla import ProcedureRecord

    count = 0
    with get_ucla_session() as session:
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
                count += 1
        session.commit()
    return count


# ── MedicationStatement ────────────────────────────────────────────


def save_medication_statements_to_db(resources: list[dict], provider: str = "ucla") -> int:
    import json
    from myhealth_fhir.db import get_ucla_session
    from myhealth_fhir.models.ucla import MedicationStatement
    from myhealth_fhir.db.ucla_unpack import extract_medication_statement

    count = 0
    with get_ucla_session() as session:
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
                count += 1
        session.commit()
    return count


# ── MedicationRequest ──────────────────────────────────────────────


def save_medication_requests_to_db(resources: list[dict], provider: str = "ucla") -> int:
    import json
    from myhealth_fhir.db import get_ucla_session
    from myhealth_fhir.models.ucla import MedicationRequest, MedicationRequestDosage, MedicationRequestIdentifier
    from myhealth_fhir.db.ucla_unpack import extract_medication_request

    count = 0
    with get_ucla_session() as session:
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
                count += 1
            session.flush()
            session.query(MedicationRequestIdentifier).filter(MedicationRequestIdentifier.medreq_id == rid).delete(synchronize_session=False)
            for row in mr_children.get("medication_request_identifier", []):
                session.add(MedicationRequestIdentifier(**row))
            session.query(MedicationRequestDosage).filter(MedicationRequestDosage.medreq_id == rid).delete(synchronize_session=False)
            for row in mr_children.get("medication_request_dosage", []):
                session.add(MedicationRequestDosage(**row))
        session.commit()
    return count


# ── AllergyIntolerance ─────────────────────────────────────────────


def save_allergies_to_db(resources: list[dict], provider: str = "ucla") -> int:
    import json
    from myhealth_fhir.db import get_ucla_session
    from myhealth_fhir.models.ucla import AllergyIntolerance
    from myhealth_fhir.db.ucla_unpack import extract_allergy

    count = 0
    with get_ucla_session() as session:
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
                count += 1
        session.commit()
    return count


# ── Immunization ───────────────────────────────────────────────────


def save_immunizations_to_db(resources: list[dict], provider: str = "ucla") -> int:
    import json
    from myhealth_fhir.db import get_ucla_session
    from myhealth_fhir.models.ucla import Immunization, ImmunizationIdentifier
    from myhealth_fhir.db.ucla_unpack import extract_immunization

    count = 0
    with get_ucla_session() as session:
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
                count += 1
            session.query(ImmunizationIdentifier).filter(ImmunizationIdentifier.immunization_id == rid).delete(synchronize_session=False)
            for row in im_children.get("immunization_identifier", []):
                session.add(ImmunizationIdentifier(**row))
        session.commit()
    return count


# ── CarePlan ───────────────────────────────────────────────────────


def save_care_plans_to_db(resources: list[dict], provider: str = "ucla") -> int:
    import json
    from myhealth_fhir.db import get_ucla_session
    from myhealth_fhir.models.ucla import CarePlan

    count = 0
    with get_ucla_session() as session:
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
                count += 1
        session.commit()
    return count


# ── MedicationAdministration (MAR) ─────────────────────────────────


def save_medication_administrations_to_db(resources: list[dict], provider: str = "ucla") -> int:
    import json
    from myhealth_fhir.db import get_ucla_session
    from myhealth_fhir.models.ucla import MedicationAdministration

    count = 0
    with get_ucla_session() as session:
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
                count += 1
        session.commit()
    return count


# ── ServiceRequest (orders) ────────────────────────────────────────


def save_service_requests_to_db(resources: list[dict], provider: str = "ucla") -> int:
    import json
    from myhealth_fhir.db import get_ucla_session
    from myhealth_fhir.models.ucla import ServiceRequest
    from myhealth_fhir.db.ucla_unpack import extract_service_request

    count = 0
    with get_ucla_session() as session:
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
                count += 1
        session.commit()
    return count


# ── Specimen ───────────────────────────────────────────────────────


def save_specimens_to_db(resources: list[dict], provider: str = "ucla") -> int:
    import json
    from myhealth_fhir.db import get_ucla_session
    from myhealth_fhir.models.ucla import Specimen
    from myhealth_fhir.db.ucla_unpack import extract_specimen

    count = 0
    with get_ucla_session() as session:
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
                count += 1
        session.commit()
    return count


# ── Communication (messages, phone encounters) ─────────────────────


def save_communications_to_db(resources: list[dict], provider: str = "ucla") -> int:
    import json
    from myhealth_fhir.db import get_ucla_session
    from myhealth_fhir.models.ucla import Communication
    from myhealth_fhir.db.ucla_unpack import extract_communication

    count = 0
    with get_ucla_session() as session:
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
                count += 1
        session.commit()
    return count


# ── CareTeam ───────────────────────────────────────────────────────


def save_care_teams_to_db(resources: list[dict], provider: str = "ucla") -> int:
    import json
    from myhealth_fhir.db import get_ucla_session
    from myhealth_fhir.models.ucla import CareTeam, CareTeamParticipant
    from myhealth_fhir.db.ucla_unpack import extract_care_team

    count = 0
    with get_ucla_session() as session:
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
                count += 1
            session.query(CareTeamParticipant).filter(CareTeamParticipant.care_team_id == rid).delete(synchronize_session=False)
            for row in ct_children.get("care_team_participant", []):
                session.add(CareTeamParticipant(**row))
        session.commit()
    return count


def save_document_references_to_db(resources: list[dict], provider: str = "ucla") -> int:
    import json
    from myhealth_fhir.db import get_ucla_session
    from myhealth_fhir.models.ucla import DocumentReference, DocumentReferenceContent, DocumentReferenceIdentifier
    from myhealth_fhir.db.ucla_unpack import extract_document_reference, upgrade_doc_displays

    count = 0
    with get_ucla_session() as session:
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
                count += 1
            session.query(DocumentReferenceContent).filter(DocumentReferenceContent.doc_id == rid).delete(synchronize_session=False)
            for row in dr_children.get("document_reference_content", []):
                session.add(DocumentReferenceContent(**row))
            session.query(DocumentReferenceIdentifier).filter(DocumentReferenceIdentifier.doc_id == rid).delete(synchronize_session=False)
            for row in dr_children.get("document_reference_identifier", []):
                session.add(DocumentReferenceIdentifier(**row))
        session.commit()
    return count


# ── FamilyMemberHistory ────────────────────────────────────────────


def save_family_member_histories_to_db(resources: list[dict], provider: str = "ucla") -> int:
    import json
    from myhealth_fhir.db import get_ucla_session
    from myhealth_fhir.models.ucla import FamilyMemberHistory
    from myhealth_fhir.db.ucla_unpack import extract_family_member_history

    count = 0
    with get_ucla_session() as session:
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
                count += 1
        session.commit()
    return count


# ── ClinicalObservation (vitals, surveys, etc.) ────────────────────


def save_clinical_observations_to_db(resources: list[dict], provider: str = "ucla") -> int:
    import json
    from myhealth_fhir.db import get_ucla_session
    from myhealth_fhir.models.ucla import ClinicalObservation, ClinicalObservationComponent
    from myhealth_fhir.db.ucla_unpack import extract_clinical_observation

    count = 0
    with get_ucla_session() as session:
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
                count += 1
            session.flush()
            obs = session.get(ClinicalObservation, rid)
            session.query(ClinicalObservationComponent).filter(ClinicalObservationComponent.observation_id == obs.id).delete(synchronize_session=False)
            for row in co_children.get("clinical_observation_component", []):
                session.add(ClinicalObservationComponent(observation_id=obs.id, **row))
        session.commit()
    return count


# ── ClinicalNote (via DocumentReference → Binary) ──────────────


def save_clinical_notes_from_docs(client, resources, headers, provider: str = "ucla") -> int:
    """Store clinical note bodies from Epic FHIR into the ``clinical_note`` table.

    Epic/UCLA does not expose a ``ClinicalNote`` resource type (the endpoint
    returns 404). Notes are delivered as ``DocumentReference`` resources with
    ``category`` code ``clinical-note``; their full text lives in a ``Binary``
    attachment referenced by ``content[].attachment.url`` (``Binary/<id>``).

    This fetches each such Binary (base64-encoded HTML), stores the note body
    in ``clinical_note`` (source='fhir', source_id=<document id>) alongside the
    metadata row saved by ``save_document_references_to_db``.

    Skips DocumentReferences that are not clinical notes (e.g. Summary
    Documents) and notes whose Binary body cannot be fetched.
    """
    import base64
    import json

    import httpx

    from myhealth_fhir.db import get_ucla_session
    from myhealth_fhir.models.ucla import ClinicalNote, ClinicalNoteIdentifier, Encounter
    from myhealth_fhir.db.ucla_unpack import extract_clinical_note, upgrade_doc_displays

    def _is_clinical_note(doc: dict) -> bool:
        for c in doc.get("category", []):
            if not isinstance(c, dict):
                continue
            text = c.get("text") or ""
            for coding in c.get("coding", []):
                if isinstance(coding, dict) and (coding.get("code") == "clinical-note" or "clinical note" in (coding.get("display") or "").lower()):
                    return True
            if "clinical note" in text.lower():
                return True
        return False

    def _first_binary_url(doc: dict) -> str | None:
        for content in doc.get("content", []):
            if not isinstance(content, dict):
                continue
            att = content.get("attachment", {})
            if isinstance(att, dict) and att.get("url"):
                return att["url"]
        return None

    def _html_to_text(raw_html: str) -> str:
        """Parse raw HTML binary attachments into clean structured text & Markdown tables via BeautifulSoup."""
        if not raw_html:
            return ""
        import html as html_lib
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(raw_html, "html.parser")

        # 1. Convert clinical HTML tables into Markdown tables
        for table in soup.find_all("table"):
            table_text = table.get_text()
            # Drop redundant embedded 50-page raw lab tables (which are in Section II)
            if "Lab Results" in table_text or "Component Value Date" in table_text or "Latest Ref Rng" in table_text:
                table.decompose()
                continue

            # Convert table to Markdown table string
            rows = table.find_all("tr")
            matrix = []
            for tr in rows:
                if "visibility: hidden" in tr.get("style", ""):
                    continue
                cells = tr.find_all(["td", "th"])
                row_cells = []
                for cell in cells:
                    cell_txt = cell.get_text().strip().replace("&nbsp;", "").replace("\xa0", "")
                    if cell_txt and cell_txt != "•":
                        row_cells.append(cell_txt)
                if row_cells:
                    matrix.append(row_cells)

            if not matrix:
                table.decompose()
                continue

            title = ""
            if len(matrix[0]) == 1:
                title = f"**{matrix[0][0]}**\n"
                matrix = matrix[1:]

            if not matrix:
                new_tag = soup.new_tag("p")
                new_tag.string = title.strip()
                table.replace_with(new_tag)
                continue

            headers = matrix[0]
            md_lines = [title + "| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
            for row in matrix[1:]:
                if len(row) < len(headers):
                    row.extend([""] * (len(headers) - len(row)))
                md_lines.append("| " + " | ".join(row[:len(headers)]) + " |")

            new_tag = soup.new_tag("p")
            new_tag.string = "\n" + "\n".join(md_lines) + "\n"
            table.replace_with(new_tag)

        lines = []
        for element in soup.find_all(["div", "p", "h1", "h2", "h3", "h4", "li"]):
            text = element.get_text().strip()
            text = html_lib.unescape(text).replace("\xa0", " ").replace("&#8226;", "•").replace("&#176;", "°")
            if text and (not lines or text != lines[-1]):
                lines.append(text)

        if not lines:
            text = html_lib.unescape(soup.get_text())
            lines = [l.strip() for l in text.split("\n") if l.strip()]

        return "\n".join(lines).strip()

    count = 0
    with get_ucla_session() as session:
        for doc in resources:
            rid = doc.get("id", "")
            if not rid or not _is_clinical_note(doc):
                continue

            binary_url = _first_binary_url(doc)
            body = None
            if binary_url:
                url = binary_url if binary_url.startswith("http") else f"{client.base_url}/{binary_url}"
                try:
                    resp = httpx.get(url, headers=headers, timeout=60)
                    resp.raise_for_status()
                    binary = resp.json()
                    data = binary.get("data") or ""
                    if data:
                        raw = base64.b64decode(data)
                        raw_html_str = raw.decode("utf-8", errors="replace")
                        body = _html_to_text(raw_html_str)
                except (httpx.HTTPError, ValueError, KeyError, base64.binascii.Error) as e:
                    log.warning("ClinicalNote %s: Binary fetch failed (%s: %s)", rid, url, e)

            type_display = (
                doc.get("type", {}).get("coding", [{}])[0].get("display")
                if isinstance(doc.get("type"), dict) and doc.get("type", {}).get("coding")
                else (doc.get("type", {}).get("text") if isinstance(doc.get("type"), dict) else None)
            )
            author_obj = doc.get("author", [{}])[0] if doc.get("author") and isinstance(doc["author"][0], dict) else None
            author_ref = _save_actor(session, author_obj, provider, "Practitioner", rid)
            authored_dt = _parse_dt(doc.get("date"))
            cn_fields, cn_children = extract_clinical_note(doc)
            upgrade_doc_displays(session, cn_fields, doc, provider)

            existing = session.query(ClinicalNote).filter(ClinicalNote.fhir_id == rid).first()
            if existing:
                existing.resource_type = "ClinicalNote"
                existing.category = "clinical-note"
                existing.title = type_display or existing.title or "Clinical Note"
                existing.text_body = body or existing.text_body
                existing.raw_html = raw_html_str or existing.raw_html
                existing.author_ref = author_ref
                existing.authored_datetime = authored_dt
                existing.source = "fhir"
                existing.source_id = rid
                existing.raw_json = json.dumps(doc) if existing.raw_json is None else existing.raw_json
                for k, v in cn_fields.items():
                    setattr(existing, k, v)
            else:
                encounter_refs = doc.get("context", {}).get("encounter", [{}]) if isinstance(doc.get("context"), dict) else []
                encounter_id = None
                if encounter_refs and isinstance(encounter_refs[0], dict):
                    ref = (encounter_refs[0].get("reference") or "").split("/")[-1]
                    if ref and session.get(Encounter, ref) is not None:
                        encounter_id = ref
                session.add(ClinicalNote(
                    fhir_id=rid,
                    encounter_id=encounter_id,
                    resource_type="ClinicalNote",
                    category="clinical-note",
                    title=type_display or "Clinical Note",
                    text_body=body,
                    raw_html=raw_html_str,
                    author_ref=author_ref,
                    authored_datetime=authored_dt,
                    source="fhir",
                    source_id=rid,
                    raw_json=json.dumps(doc),
                    **cn_fields,
                ))
                count += 1
            session.query(ClinicalNoteIdentifier).filter(ClinicalNoteIdentifier.note_id == rid).delete(synchronize_session=False)
            for row in cn_children.get("clinical_note_identifier", []):
                session.add(ClinicalNoteIdentifier(**row))
        session.commit()
    return count


def backfill_clinical_note_attachments(client, provider: str = "ucla", batch_size: int = 25, limit: int | None = None, workers: int = 8) -> dict[str, int]:
    """Backfill missing HTML/RTF note bodies from Epic FHIR attachments.

    This intentionally re-reads each stored ``DocumentReference`` and walks
    every ``content[].attachment`` entry. Epic exposes HTML and RTF as separate
    Binary resources, so selecting only the first attachment loses RTF.
    Existing non-NULL columns are never overwritten.
    """
    import base64
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from myhealth_fhir.db import get_ucla_session
    from myhealth_fhir.models.ucla import ClinicalNote

    stats = {"notes": 0, "updated": 0, "html": 0, "rtf": 0, "failed": 0}
    with get_ucla_session() as session:
        rows = session.query(ClinicalNote.id, ClinicalNote.fhir_id, ClinicalNote.raw_html, ClinicalNote.raw_rtf, ClinicalNote.raw_json).filter(
            ClinicalNote.source == "fhir",
            (ClinicalNote.raw_html.is_(None) | ClinicalNote.raw_rtf.is_(None)),
        ).order_by(ClinicalNote.id).limit(limit).all()
    stats["notes"] = len(rows)
    def fetch(row):
        note_id, fhir_id, raw_html, raw_rtf, raw_json = row
        patient_id = None
        try:
            raw_doc = json.loads(raw_json or "{}")
            patient_id = _extract_patient_id(raw_doc)
            doc = client.get("DocumentReference", fhir_id, patient_id=patient_id)
            bodies: dict[str, str] = {}
            for content in doc.get("content", []):
                attachment = content.get("attachment", {}) if isinstance(content, dict) else {}
                content_type = (attachment.get("contentType") or "").lower().split(";", 1)[0]
                if content_type not in {"text/html", "text/rtf", "application/rtf"}:
                    continue
                url = attachment.get("url")
                if not url:
                    continue
                binary = client.get("Binary", url.rstrip("/").split("/")[-1], patient_id=patient_id)
                if binary.get("data"):
                    bodies["rtf" if content_type in {"text/rtf", "application/rtf"} else "html"] = base64.b64decode(binary["data"]).decode("utf-8", errors="replace")
            return row, bodies, None
        except Exception as exc:
            return row, {}, exc

    results = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(fetch, row) for row in rows]
        for future in as_completed(futures):
            results.append(future.result())

    for start in range(0, len(results), batch_size):
        with get_ucla_session() as session:
            for row, bodies, exc in results[start:start + batch_size]:
                note_id, fhir_id, old_html, old_rtf, _ = row
                if exc:
                    stats["failed"] += 1
                    log.warning("ClinicalNote %s attachment backfill failed: %s", fhir_id, exc)
                    continue
                note = session.get(ClinicalNote, note_id)
                if not note:
                    continue
                patient_id = None
                if note.raw_html is None and bodies.get("html"):
                    note.raw_html = bodies["html"]
                    stats["html"] += 1
                if note.raw_rtf is None and bodies.get("rtf"):
                    note.raw_rtf = bodies["rtf"]
                    stats["rtf"] += 1
                if (note.raw_html != old_html) or (note.raw_rtf != old_rtf):
                    stats["updated"] += 1
            session.commit()
    return stats

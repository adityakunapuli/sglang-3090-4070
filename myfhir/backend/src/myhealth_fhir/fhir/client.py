"""FHIR client for Anthem/Elevance Health TotalView API."""


import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fhirpy import SyncFHIRClient
from fhirpy.base.exceptions import MultipleResourcesFound, ResourceNotFound

from myhealth_fhir.fhir.parsing import (
    _clean_error_message,
    _fh_error_text,
    _max_last_updated,
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
                from myhealth_fhir.fhir.anthem_save import save_eobs_to_db

                saved_count = save_eobs_to_db(self, eobs)
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
        import httpx

        from myhealth_fhir.fhir.ucla_save import save_imaging_observations, save_labs_to_db

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

        from myhealth_fhir.fhir.ucla_save import (
            save_allergies_to_db,
            save_care_plans_to_db,
            save_care_teams_to_db,
            save_clinical_observations_to_db,
            save_communications_to_db,
            save_conditions_to_db,
            save_encounters_to_db,
            save_family_member_histories_to_db,
            save_immunizations_to_db,
            save_medication_administrations_to_db,
            save_medication_requests_to_db,
            save_medication_statements_to_db,
            save_procedures_to_db,
            save_service_requests_to_db,
            save_specimens_to_db,
        )

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
                    from myhealth_fhir.fhir.ucla_save import save_imaging_observations, save_labs_to_db
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
                    from myhealth_fhir.fhir.notes import save_clinical_notes_from_docs
                    from myhealth_fhir.fhir.ucla_save import save_document_references_to_db
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
        from sqlalchemy import func

        from myhealth_fhir.db import get_anthem_session
        from myhealth_fhir.db.identity import entity_ref
        from myhealth_fhir.models.anthem import EOB

        with get_anthem_session() as session:
            return session.query(func.count(EOB.id)).filter(
                EOB.patient_ref == entity_ref("anthem", "Patient", patient_id)
            ).scalar() or 0

    def _count_claims(self, patient_id: str) -> int:
        """Return total ClaimSubmission row count in the DB for a patient."""
        from sqlalchemy import func

        from myhealth_fhir.db import get_anthem_session
        from myhealth_fhir.db.identity import entity_ref
        from myhealth_fhir.models.anthem import ClaimSubmission

        with get_anthem_session() as session:
            return session.query(func.count(ClaimSubmission.id)).filter(
                ClaimSubmission.patient_ref == entity_ref("anthem", "Patient", patient_id)
            ).scalar() or 0

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
                from myhealth_fhir.fhir.anthem_save import save_claims_to_db

                saved_count = save_claims_to_db(self, claims)
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
        from myhealth_fhir.models.anthem import EOB, ClaimSubmission

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



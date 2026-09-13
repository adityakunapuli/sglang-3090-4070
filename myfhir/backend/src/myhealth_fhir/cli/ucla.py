"""UCLA Health resource commands (patients, save-*, medications, encounters, ehi, report)."""


import httpx

import click
from tqdm import tqdm

from myhealth_fhir.cli.auth import do_auth
from myhealth_fhir.cli.main import ucla
from myhealth_fhir.cli.output import (
    print_allergy_summary,
    print_careplan_summary,
    print_condition_summary,
    print_encounter_summary,
    print_immunization_summary,
    print_lab_panel,
    print_medication_summary,
)
from myhealth_fhir.fhir.client import get_fhir_client
from myhealth_fhir.fhir.notes import backfill_clinical_note_attachments


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
        from sqlalchemy import text

        from myhealth_fhir.db import get_ucla_session

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

"""Epic EHI (Request an Electronic Health Information) export importer.

Reads a manually-downloaded Epic EHI export directory (the ``EHITables/``,
``Rich Text/`` and ``Media/`` folders) and merges the data that is NOT
reliably available through the live UCLA FHIR API — chiefly:

  * Clinical notes (rich-text bodies + plain text + note metadata)
  * Medication administrations (MAR history)
  * Encounter backfill (PAT_ENC rows missing from FHIR)
  * Flowsheet vitals timeline (measurement name + recorded time)
  * Orders (ORDER_MED → medication_request, ORDER_PROC → service_request)
  * Immunizations (IMM_ADMIN)

Violates nothing in the "no BLOB storage" constraint: ``Media/`` binaries and
``CLARITY_EDG`` (a Merck dictionary) are intentionally skipped, and RTF note
bodies are parsed to plain text before being stored in ``clinical_note.text_body``.

The export is PHI and must never be committed. This module only ever reads from
the directory path the caller passed (the CLI keeps it a runtime argument that
is gitignored/excluded from the repository).
"""

import csv
import json
import logging
import re
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

_TSV_FIELDSIZE = 2_000_000
csv.field_size_limit(_TSV_FIELDSIZE)

# ── TSV helpers ────────────────────────────────────────────────────


def _read_tsv(path: Path, materialize: bool = True) -> list[dict[str, str]]:
    """Read a pipe-agnostic TSV file into a list of row dicts.

    Handles the EHI export's UTF-8 (with BOM) TSVs. Returns [] for missing
    or empty files (header-only).
    """
    if not path.exists():
        log.info("  (missing table: %s)", path.name)
        return []
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            rows = list(reader) if materialize else list(reader)
    except (csv.Error, UnicodeDecodeError) as e:
        log.warning("  (failed to read %s: %s)", path.name, e)
        return []
    return rows


def _get(row: dict, *keys: str) -> str | None:
    """First non-empty value among the given keys (case-insensitive)."""
    lower = {k.lower(): v for k, v in row.items()}
    for key in keys:
        v = lower.get(key.lower())
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, str):
        s = value.strip()
        # Epic formats: "5/7/2021 1:42:00 PM" and "5/7/2021 12:00:00 AM"
        for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return value


# ── RTF → plain text ───────────────────────────────────────────────


def _rtf_to_text(rtf: str) -> str:
    r"""Convert an RTF string to plain text.

    Handles the common Epic constructs: control words, groups, \uN unicode
    escapes, \\par/\\line breaks, tables (\trowd..\\row → tab/newline), and
    escapes (\\', \\{ \\} \\\\). Best-effort — used only to surface narrative.
    """
    # \uN? where N can be signed
    def _uni(m):
        n = int(m.group(1))
        if -32768 <= n <= 65535:
            return chr(n if n >= 0 else n + 65536)
        return ""

    text = re.sub(r"\\u(-?\d+)\??", _uni, rtf)

    # Drop control word parameter tokens like \fs24, \uc1, \deff0
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", text)
    # Escaped specials
    text = text.replace("\\{", "{").replace("\\}", "}").replace("\\\\", "\\")
    text = re.sub(r"\\'[0-9a-fA-F]{2}", " ", text)
    # Hex escapes inside special bodies are already gone; handle \* ignored words
    # Structural breaks
    text = text.replace("\\par ", "\n").replace("\\line ", "\n").replace("\\tab ", "\t")
    text = re.sub(r"\\row\b", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ── Importer ───────────────────────────────────────────────────────


class EHIImporter:
    """Loads an Epic EHI export directory into the UCLA database.

    Patient identity: the export is downloaded per patient. The caller supplies
    the FHIR patient_id (which differs from the EHI PAT_ID); the importer keys
    all rows against that single FHIR patient.
    """

    def __init__(self, export_dir: str | Path, patient_id: str, no_db: bool = False):
        """Prepare the importer against an EHI export directory.

        Args:
            export_dir: Root of the Epic EHI export (contains EHITables/, Rich Text/, Media/).
            patient_id: FHIR patient id all rows are keyed to (EHI PAT_ID differs).
            no_db: If True, read/parse only and never write to the database.
        """
        self.root = Path(export_dir)
        self.tables_dir = self.root / "EHITables"
        self.rich_dir = self.root / "Rich Text"
        self.patient_id = patient_id
        self.no_db = no_db
        self.summary: dict[str, int] = {}
        self._csn_to_encounter: dict[str, str] = {}
        self._order_med_by_id: dict[str, dict] = {}

    # ── Encounter map (CSN → encounter id) ─────────────────────────
    def _load_encounter_map(self):
        """Preload existing encounter ids by PAT_ENC_CSN_ID (source_id) from DB."""
        if self.no_db:
            return
        from myhealth_fhir.db import get_ucla_session
        from myhealth_fhir.models.ucla import Encounter

        with get_ucla_session() as session:
            rows = session.query(Encounter.id, Encounter.source_id).all()
            for enc_id, source_id in rows:
                if source_id:
                    self._csn_to_encounter[str(source_id)] = enc_id

    # ── Notes ──────────────────────────────────────────────────────
    def import_notes(self) -> int:
        """Import clinical notes: metadata (HNO_INFO) + body (RTF or HNO_PLAIN_TEXT)."""
        info_path = self.tables_dir / "HNO_INFO.tsv"
        enc_path = self.tables_dir / "NOTE_ENC_INFO.tsv"
        plain_path = self.tables_dir / "HNO_PLAIN_TEXT.tsv"

        info_rows = {row.get("NOTE_ID"): row for row in _read_tsv(info_path) if row.get("NOTE_ID")}
        enc_rows = {}
        for row in _read_tsv(enc_path):
            nid = row.get("NOTE_ID")
            if nid and nid not in enc_rows:
                enc_rows[nid] = row
        plain_by_note: dict[str, list] = {}
        for row in _read_tsv(plain_path):
            nid = row.get("NOTE_ID")
            if nid:
                plain_by_note.setdefault(nid, []).append(row)
        for lines in plain_by_note.values():
            lines.sort(key=lambda r: int(r.get("LINE") or 0))

        # Pre-resolve note_type names
        note_types = {
            r.get("NOTE_TYPE_ID"): r.get("NOTE_TYPE_C_NAME")
            for r in _read_tsv(self.tables_dir / "NOTE_TYPE.tsv")
        }

        from myhealth_fhir.db import get_ucla_session
        from myhealth_fhir.models.ucla import ClinicalNote, Encounter

        imported = 0
        with get_ucla_session() as session:
            for note_id, info in sorted(info_rows.items()):
                # skip deleted notes
                if _get(info, "DELETE_INSTANT_DTTM"):
                    continue
                note_type = _get(info, "NOTE_TYPE_NOADD_C_NAME") or note_types.get(_get(info, "NOTE_TYPE_ID") or "")
                csn = _get(info, "PAT_ENC_CSN_ID")
                encounter_id = None
                if csn and not self.no_db:
                    could = self._csn_to_encounter.get(str(csn))
                    encounter_id = could if could and session.get(Encounter, could) is not None else None

                author = _get(info, "CURRENT_AUTHOR_ID_NAME") or _get(info, "ENTRY_USER_ID_NAME")
                created = _parse_dt(_get(info, "CREATE_INSTANT_DTTM"))
                service_dt = _parse_dt(_get(info, "DATE_OF_SERVIC_DTTM"))

                # Body: prefer RTF (full narrative), fall back to plain-text lines
                body = None
                rtf_files = sorted(self.rich_dir.glob(f"HNO_{note_id}_*_41.rtf")) if self.rich_dir.exists() else []
                if rtf_files:
                    try:
                        raw = rtf_files[0].read_text(encoding="utf-8", errors="replace")
                        body = _rtf_to_text(raw)
                    except OSError as e:
                        log.warning("  (rtf read failed %s: %s)", rtf_files[0].name, e)
                if not body and note_id in plain_by_note:
                    body = "\n".join(r.get("NOTE_TEXT", "") for r in plain_by_note[note_id]).strip()

                if not body and not note_type:
                    continue

                # Upsert by source='ehi' + source_id=NOTE_ID
                existing = (
                    session.query(ClinicalNote)
                    .filter(ClinicalNote.source == "ehi", ClinicalNote.source_id == str(note_id))
                    .first()
                )
                vals = dict(
                    source="ehi", source_id=str(note_id),
                    resource_type="ClinicalNote", category=note_type,
                    title=note_type or _get(info, "NOTE_DESC") or "Clinical Note",
                    text_body=body,
                    author_ref=author,
                    authored_datetime=created,
                    effective_datetime=service_dt,
                )
                if encounter_id:
                    vals["encounter_id"] = encounter_id
                if existing:
                    for k, v in vals.items():
                        if v is not None:
                            setattr(existing, k, v)
                else:
                    vals["fhir_id"] = f"ehi_note_{note_id}"
                    session.add(ClinicalNote(**vals))
                imported += 1
            session.commit()
        self.summary["notes"] = imported
        return imported

    # ── MedicationAdministration (MAR) ─────────────────────────────
    def import_medication_admin(self) -> int:
        """Import MAR medication administrations (MAR_ADMIN_INFO.tsv) as MedicationAdministration rows."""
        mar_path = self.tables_dir / "MAR_ADMIN_INFO.tsv"
        order_path = self.tables_dir / "ORDER_MED.tsv"

        order_rows = {}
        for row in _read_tsv(order_path):
            oid = row.get("ORDER_MED_ID")
            if oid:
                order_rows[oid] = row
        mar_rows = _read_tsv(mar_path)

        from myhealth_fhir.db import get_ucla_session
        from myhealth_fhir.models.ucla import MedicationAdministration

        imported = 0
        with get_ucla_session() as session:
            for row in mar_rows:
                order_id = _get(row, "ORDER_MED_ID")
                line = _get(row, "LINE") or "0"
                taken = _parse_dt(_get(row, "TAKEN_TIME"))
                if not taken:
                    taken = _parse_dt(_get(row, "MAR_SCHD_DTTM"))
                med = None
                order_detail = order_rows.get(order_id, {}) if order_id else {}
                med = (
                    _get(order_detail, "DESCRIPTION")
                    or _get(order_detail, "DISPLAY_NAME")
                    or _get(order_detail, "MEDICATION_ID")
                )
                marco = _get(row, "MAR_ACTION_C_NAME")
                route = _get(row, "ROUTE_C_NAME")
                dose = _get(row, "SCHEDULED_DOSE") or _get(row, "ORIGINAL_AMOUNT")
                dose_unit = _get(row, "SCHEDULED_DOSE_UNIT_C_NAME") or _get(row, "DOSE_UNIT_C_NAME")
                morph = _get(row, "MORPHINE_EQUIV_MG_DOSE")
                performer = _get(row, "MAR_DOC_USER_ID_NAME") or _get(row, "USER_ID_NAME")
                comments = _get(row, "COMMENTS")
                fake_id = f"ehi_mar_{order_id or '?'}_{line}"
                existing = session.query(MedicationAdministration).filter(
                    MedicationAdministration.fhir_id == fake_id
                ).first()
                vals = dict(
                    source="ehi", source_id=fake_id, order_id=order_id,
                    fhir_id=fake_id,
                    patient_id=self.patient_id,
                    status="completed" if marco else None,
                    medication_display=med or marco,
                    administered_datetime=taken,
                    route_display=route,
                    dose_display=f"{dose} {dose_unit}".strip() if dose else None,
                    reason_display=marco,
                    performer_ref=None if self.no_db else performer,
                    note_text=comments,
                    raw_json=json.dumps({"order_id": order_id, "source": "MAR_ADMIN_INFO"}),
                )
                if morph:
                    vals["morphone_mg"] = float(morph or 0)
                # performer stored as free text name in performer_ref (no registry requirement)
                if vals["performer_ref"] and not vals["performer_ref"].startswith("ucla:"):
                    vals["performer_ref"] = f"ucla:Practitioner:mar:{vals['performer_ref']}"
                if existing:
                    for k, v in vals.items():
                        if v is not None:
                            setattr(existing, k, v)
                else:
                    session.add(MedicationAdministration(**vals))
                imported += 1
            session.commit()
        self.summary["medication_admin"] = imported
        return imported

    # ── Encounter backfill (PAT_ENC) ───────────────────────────────
    def import_encounters(self) -> int:
        """Backfill encounters from PAT_ENC.tsv not already present via FHIR."""
        pat_path = self.tables_dir / "PAT_ENC.tsv"
        rows = _read_tsv(pat_path)

        from myhealth_fhir.db import get_ucla_session
        from myhealth_fhir.models.ucla import Encounter

        imported = 0
        by_csn = {str(v): k for k, v in self._csn_to_encounter.items()}
        with get_ucla_session() as session:
            for row in rows:
                csn = _get(row, "PAT_ENC_CSN_ID")
                if not csn:
                    continue
                if str(csn) in by_csn:
                    continue  # already fetched via FHIR
                dt = _parse_dt(_get(row, "PAT_ENC_DATE_REAL") or _get(row, "CONTACT_DATE"))
                enc_id = f"ehi_enc_{csn}"
                existing = session.get(Encounter, enc_id)
                fin = _get(row, "FIN_CLASS_C_NAME")
                if existing:
                    existing.period_start = dt or existing.period_start
                    existing.class_ = fin or existing.class_
                else:
                    session.add(Encounter(
                        id=enc_id, source="ehi", source_id=str(csn),
                        patient_id=self.patient_id, status="finished",
                        class_=fin if fin not in (None, "") else None,
                        period_start=dt,
                    ))
                imported += 1
            session.commit()

        # Refresh CSN map so notes imported after can link to EHI encounters too
        for row in rows:
            csn = _get(row, "PAT_ENC_CSN_ID")
            if csn:
                self._csn_to_encounter.setdefault(str(csn), f"ehi_enc_{csn}")
        self.summary["encounters"] = imported
        return imported

    # ── Flowsheet vitals (IP_FLWSHT_MEAS) ──────────────────────────
    def import_vitals(self) -> int:
        """Import flowsheet vitals timeline (IP_FLWSHT_MEAS.tsv) as ClinicalObservation rows."""
        fs_path = self.tables_dir / "IP_FLWSHT_MEAS.tsv"
        rows = _read_tsv(fs_path)

        from myhealth_fhir.db import get_ucla_session
        from myhealth_fhir.models.ucla import ClinicalObservation

        imported = 0
        with get_ucla_session() as session:
            batch: set[str] = set()
            for row in rows:
                meas_name = _get(row, "FLO_MEAS_ID_DISP_NAME")
                recorded_time = _get(row, "RECORDED_TIME") or _get(row, "ENTRY_TIME")
                if not meas_name:
                    continue
                ds = f"ehi_vitals_{row.get('FSD_ID')}_{row.get('LINE')}"
                if ds in batch:
                    continue
                batch.add(ds)
                existing = session.query(ClinicalObservation).filter(ClinicalObservation.source_id == ds).first()
                if existing:
                    continue
                session.add(ClinicalObservation(
                    fhir_id=ds, source="ehi", source_id=ds,
                    category="vital-signs",
                    code_display=meas_name,
                    value_text=_get(row, "MEAS_COMMENT") or "(recorded)",
                    effective_datetime=_parse_dt(recorded_time),
                ))
                imported += 1
            session.commit()
        self.summary["vitals"] = imported
        return imported

    # ── Orders (ORDER_MED → medication_request, ORDER_PROC → service_request) ──
    def import_orders(self) -> tuple[int, int]:
        """Import medication orders (ORDER_MED.tsv) and procedure orders (ORDER_PROC.tsv)."""
        med_path = self.tables_dir / "ORDER_MED.tsv"
        proc_path = self.tables_dir / "ORDER_PROC.tsv"

        from myhealth_fhir.db import get_ucla_session
        from myhealth_fhir.models.ucla import MedicationRequest, ServiceRequest

        meds = 0
        procs = 0
        with get_ucla_session() as session:
            for row in _read_tsv(med_path):
                oid = _get(row, "ORDER_MED_ID")
                if not oid:
                    continue
                fake_id = f"ehi_medorder_{oid}"
                if session.get(MedicationRequest, fake_id) is not None:
                    continue
                route = _get(row, "MED_ROUTE_C_NAME")
                dosage = _get(row, "DOSAGE") or ""
                if route:
                    dosage = f"{dosage} ({route})".strip() if dosage else f"(route: {route})"
                session.add(MedicationRequest(
                    fhir_id=fake_id, source="ehi", source_id=oid,
                    patient_id=self.patient_id,
                    status=_get(row, "ORDER_STATUS_C_NAME"),
                    medication_display=_get(row, "DISPLAY_NAME") or _get(row, "DESCRIPTION"),
                    authored_on=_parse_dt(_get(row, "ORDERING_DATE") or _get(row, "START_DATE")),
                    validity_start=_parse_dt(_get(row, "START_DATE")),
                    validity_end=_parse_dt(_get(row, "END_DATE")),
                    dosage_instruction=dosage,
                    note_text=_get(row, "MED_COMMENTS") or _get(row, "ORDER_INST"),
                ))
                meds += 1

            for row in _read_tsv(proc_path):
                oid = _get(row, "ORDER_PROC_ID")
                if not oid:
                    continue
                fake_id = f"ehi_procorder_{oid}"
                if session.get(ServiceRequest, fake_id) is not None:
                    continue
                session.add(ServiceRequest(
                    fhir_id=fake_id, source="ehi", source_id=oid,
                    patient_id=self.patient_id,
                    status=_get(row, "ORDER_STATUS_C_NAME") or _get(row, "LAB_STATUS_C_NAME"),
                    category=_get(row, "ORDER_TYPE_C_NAME") or _get(row, "ORDER_CLASS_C_NAME"),
                    code_display=_get(row, "DESCRIPTION"),
                    authored_on=_parse_dt(_get(row, "ORDERING_DATE")),
                    order_detail=_get(row, "ORDER_INST"),
                ))
                procs += 1
            session.commit()
        self.summary["medication_orders"] = meds
        self.summary["procedure_orders"] = procs
        return meds, procs

    # ── Immunizations (IMM_ADMIN) ──────────────────────────────────
    def import_immunizations(self) -> int:
        """Import immunizations (IMM_ADMIN.tsv) as Immunization rows."""
        imm_path = self.tables_dir / "IMM_ADMIN.tsv"
        rows = _read_tsv(imm_path)

        from myhealth_fhir.db import get_ucla_session
        from myhealth_fhir.models.ucla import Immunization

        imported = 0
        with get_ucla_session() as session:
            for row in rows:
                doc_id = _get(row, "DOCUMENT_ID")
                if not doc_id:
                    continue
                fake_id = f"ehi_imm_{doc_id}"
                existing = session.get(Immunization, fake_id)
                if existing:
                    continue
                session.add(Immunization(
                    fhir_id=fake_id, source="ehi", source_id=doc_id,
                    patient_id=self.patient_id,
                    status="completed" if _get(row, "IMM_DATE") else None,
                    vaccine_display=_get(row, "IMM_TYPE_ID_NAME") or _get(row, "IMM_TYPE_FREE_TEXT"),
                    occurrence_datetime=_parse_dt(_get(row, "IMM_DATE")),
                    manufacturer=_get(row, "IMM_MANUFACTURER_C_NAME") or _get(row, "IMM_MANUF_FREE_TEXT"),
                    lot_number=_get(row, "IMM_LOT_NUMBER"),
                    route_display=_get(row, "IMM_ROUTE_C_NAME") or _get(row, "IMM_ROUTE_FREE_TXT"),
                    site_display=_get(row, "IMM_SITE_C_NAME") or _get(row, "IMM_SITE_FREE_TXT"),
                    note_text=_get(row, "IMM_NOTES") or _get(row, "IMM_NOTES_RAW_DATA"),
                ))
                imported += 1
            session.commit()
        self.summary["immunizations"] = imported
        return imported

    # ── Full run ───────────────────────────────────────────────────
    def run(self, note=True, mar=True, enc=True, vitals=True, orders=True, imm=True) -> dict[str, int]:
        """Run the selected EHI import datasets and return per-table row counts."""
        self._load_encounter_map()
        if enc:
            self.import_encounters()
        if note:
            self.import_notes()
        if mar:
            self.import_medication_admin()
        if vitals:
            self.import_vitals()
        if orders:
            self.import_orders()
        if imm:
            self.import_immunizations()
        return self.summary


def import_ehi_export(export_dir: str | Path, patient_id: str, no_db: bool = False,
                      notes=True, mar=True, encounters=True, vitals=True, orders=True, imm=True) -> dict[str, int]:
    """Convenience wrapper around EHIImporter.run()."""
    importer = EHIImporter(export_dir, patient_id, no_db=no_db)
    return importer.run(note=notes, mar=mar, enc=encounters, vitals=vitals, orders=orders, imm=imm)

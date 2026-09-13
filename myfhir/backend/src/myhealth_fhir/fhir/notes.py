"""Clinical-note persistence (DocumentReference-driven notes + backfill).

Moved verbatim from services/fhir_client.py (plan 4c).
"""

import json
import logging

from myhealth_fhir.fhir.parsing import (
    _extract_patient_id,
    _parse_dt,
)
from myhealth_fhir.fhir.ucla_save import _save_actor

log = logging.getLogger(__name__)


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

    import httpx

    from myhealth_fhir.db import get_session_for
    from myhealth_fhir.db.ucla_unpack import extract_clinical_note, upgrade_doc_displays
    from myhealth_fhir.models.ucla import ClinicalNote, ClinicalNoteIdentifier, Encounter

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
    with get_session_for(provider) as session:
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

    from myhealth_fhir.db import get_session_for
    from myhealth_fhir.models.ucla import ClinicalNote

    stats = {"notes": 0, "updated": 0, "html": 0, "rtf": 0, "failed": 0}
    with get_session_for(provider) as session:
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
        with get_session_for(provider) as session:
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

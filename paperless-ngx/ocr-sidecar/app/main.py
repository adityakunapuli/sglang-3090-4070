from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import signal
import sys
from contextlib import asynccontextmanager
from typing import Any

import httpx

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from app.ai import AiAnalyzer
from app.config import Config
from app.database import Database
from app.ocr import OcrProcessor
from app.paperless import PaperlessClient

logger = logging.getLogger("ocr.main")

# ── Global scan lock (no overlapping scans) ──────────────────────────────
_scan_lock = asyncio.Lock()
_scan_in_progress = False
_reprocess_semaphore = asyncio.Semaphore(5)
_reprocessing_tasks = []


async def process_document(
    doc: dict,
    paperless: PaperlessClient,
    ocr: OcrProcessor | None,
    ai: AiAnalyzer | None,
    db: Database,
    config: Config,
    processed_tag_id: int | None = None,
) -> str:
    doc_id = doc["id"]
    title = doc.get("title", f"doc_{doc_id}")
    mime_type = doc.get("mime_type", "")

    logger.info("Processing document %d: %s (mime_type=%s)", doc_id, title, mime_type)

    ocr_text: str | None = None
    status = "pending"
    ai_success = False

    # ── Step 1: OCR ──────────────────────────────────────────────────────
    if ocr:
        if not mime_type.startswith("application/pdf"):
            logger.info("Skipping non-PDF document %d (mime_type=%s)", doc_id, mime_type)
            await db.log_event("INFO", "skipped_non_pdf", doc_id, f"Skipped: mime_type={mime_type}")
            return "skipped"

        pdf_bytes = await paperless.download_document(doc_id)
        logger.info("Downloaded PDF for doc %d (%d bytes)", doc_id, len(pdf_bytes))

        ocr_text = await ocr.ocr_document(pdf_bytes, doc_id)
        if not ocr_text:
            logger.warning("OCR returned no text for doc %d", doc_id)
            await db.log_event("WARN", "ocr_empty", doc_id, "OCR returned no text")
            status = "failed"
        elif "[OCR failed for page" in ocr_text:
            failed_pages = ocr_text.count("[OCR failed for page")
            total_pages = failed_pages + ocr_text.count("--- Page Break ---") + 1
            logger.error(
                "OCR failed on %d/%d pages for doc %d",
                failed_pages, total_pages, doc_id,
            )
            await db.log_event(
                "ERROR", "ocr_pages_failed", doc_id,
                f"Failed {failed_pages}/{total_pages} pages"
            )
            status = "ocr_failed"

        if status not in ("failed", "ocr_failed"):
            existing = await paperless.get_existing_content(doc_id)
            if existing.strip() == ocr_text.strip():
                logger.info("OCR text unchanged for doc %d, skipping writeback", doc_id)
            else:
                await paperless.update_content(doc_id, ocr_text)
                logger.info(
                    "Content updated for doc %d (was %d chars, now %d chars)",
                    doc_id, len(existing), len(ocr_text),
                )

            status = "ocr_done"

            # ── Step 2: AI Metadata Extraction ────────────────────────────
            if ai and ocr_text:
                try:
                    analysis = await ai.analyze(ocr_text)
                    if analysis:
                        await _apply_ai_metadata(doc_id, analysis, paperless, db)
                        status = "done"
                        ai_success = True
                    else:
                        logger.warning("AI analysis returned no metadata for doc %d", doc_id)
                except Exception as e:
                    logger.error("AI analysis failed for doc %d: %s", doc_id, e, exc_info=True)
                    await db.log_event("ERROR", "ai_failed", doc_id, str(e)[:200])

    await db.upsert_document(
        paperless_id=doc_id,
        title=title,
        status=status,
        ocr_text=ocr_text if status not in ("failed", "ocr_failed") else None,
    )

    if processed_tag_id is not None and status == "done":
        try:
            await paperless.add_tag(doc_id, processed_tag_id)
        except Exception as e:
            logger.warning("Failed to add processed tag to doc %d: %s", doc_id, e)

    await db.log_event("INFO", "done", doc_id, f"Completed with status={status}")
    logger.info("Finished document %d: %s (status=%s)", doc_id, title, status)
    return status


async def _apply_ai_metadata(
    doc_id: int,
    analysis: dict[str, Any],
    paperless: PaperlessClient,
    db: Database,
) -> None:
    title = analysis.get("title")
    correspondent_name = analysis.get("correspondent")
    doc_type_name = analysis.get("document_type")
    tag_names = analysis.get("tags", [])

    # Post-process: filter out verb/action tags (tags should be nouns/categories only)
    verb_tag_patterns = [
        "submitted", "processed", "approved", "paid", "filed", "reviewed",
        "completed", "finished", "done", "pending", "rejected", "denied",
        "verified", "confirmed", "submitted", "received", "sent", "mailed",
        "emailed", "uploaded", "downloaded", "archived", "deleted", "removed",
        "added", "created", "updated", "modified", "changed", "edited",
        "reprocessed", "scanned", "ocr", "ocr_processed", "ocr_processing"
    ]
    tag_names = [t for t in tag_names if not any(v in t.lower().strip() for v in verb_tag_patterns)]

    correspondent_cache: dict[str, int] = {}
    doc_type_cache: dict[str, int] = {}
    existing_tags = await paperless.get_all_tags()

    correspondent_id = None
    if correspondent_name:
        correspondent_id = await paperless.resolve_correspondent(
            correspondent_name, correspondent_cache
        )

    document_type_id = None
    if doc_type_name:
        document_type_id = await paperless.resolve_document_type(
            doc_type_name, doc_type_cache
        )

    tag_ids = []
    for tn in (tag_names or []):
        try:
            tid = await paperless.resolve_tag(tn, existing_tags)
            tag_ids.append(tid)
        except Exception as e:
            logger.warning("Failed to resolve tag '%s': %s", tn, e)

    ai_summary = analysis.get("AI_Summary")
    ai_keydetails = analysis.get("AI_KeyDetails")
    custom_fields_map = await paperless.get_custom_fields()

    CF_MAX_LEN = 128
    custom_field_updates = []
    if ai_summary and "AI_Summary" in custom_fields_map:
        val = ai_summary[:CF_MAX_LEN - 3] + "..." if len(ai_summary) > CF_MAX_LEN else ai_summary
        custom_field_updates.append({
            "field": custom_fields_map["AI_Summary"],
            "value": val,
        })
    if ai_keydetails and "AI_KeyDetails" in custom_fields_map:
        val = ai_keydetails[:CF_MAX_LEN - 3] + "..." if len(ai_keydetails) > CF_MAX_LEN else ai_keydetails
        custom_field_updates.append({
            "field": custom_fields_map["AI_KeyDetails"],
            "value": val,
        })

    try:
        await paperless.update_metadata(
            doc_id=doc_id,
            title=title,
            correspondent=correspondent_id,
            document_type=document_type_id,
            tags=tag_ids,
            custom_fields=custom_field_updates if custom_field_updates else None,
        )
    except httpx.HTTPStatusError as e:
        logger.error(
            "Metadata PATCH failed for doc %d: HTTP %d - %s",
            doc_id, e.response.status_code, e.response.text[:500],
        )
        raise

    await db.log_event(
        "INFO", "ai_metadata", doc_id,
        f"Title={title} Correspondent={correspondent_name} Type={doc_type_name} Tags={tag_names}",
    )
    await db.upsert_document(
        paperless_id=doc_id,
        title=title or "",
        metadata_json=json.dumps(analysis, default=str),
    )


async def scan(config: Config, db: Database | None = None) -> None:
    global _scan_in_progress
    if _scan_in_progress:
        logger.info("Scan already in progress, skipping")
        return

    async with _scan_lock:
        _scan_in_progress = True
        try:
            logger.info("Starting scan cycle")
            if db is None:
                db = Database(config)
                await db.initialize()

            paperless = PaperlessClient(config)
            ocr = OcrProcessor(config) if not config.ocr_api_url.startswith("http://none") else None
            ai = AiAnalyzer(config) if config.ai_analysis_enabled else None

            try:
                skip_tag_id = None
                processed_tag_id = None
                ai_processed_tag_id = None
                if config.skip_tag:
                    skip_tag_id = await paperless.get_tag_id(config.skip_tag)
                if config.processed_tag:
                    processed_tag_id = await paperless.ensure_tag(config.processed_tag)
                if config.ai_processed_tag:
                    ai_processed_tag_id = await paperless.get_tag_id(config.ai_processed_tag)

                # Phase 1: Full processing (OCR + AI) for docs without processed tag
                docs = await paperless.get_all_documents(skip_tag_id)
                if config.only_doc_ids:
                    docs = [d for d in docs if d["id"] in config.only_doc_ids]
                logger.info("Found %d documents for full processing (OCR + AI)", len(docs))

                for i, doc in enumerate(docs):
                    logger.info("[%d/%d] Processing document %d", i + 1, len(docs), doc["id"])
                    try:
                        status = await process_document(doc, paperless, ocr, ai, db, config, processed_tag_id)
                    except Exception as e:
                        logger.error(
                            "Failed to process document %d: %s", doc["id"], e, exc_info=True
                        )
                        await db.upsert_document(
                            paperless_id=doc["id"],
                            title=doc.get("title", ""),
                            status="failed",
                            error=str(e)[:500],
                        )
                        await db.log_event("ERROR", "failed", doc["id"], str(e)[:200])

                # Phase 2: AI-only reprocessing for docs with ocr-processed but missing ai-processed tag
                if ai_processed_tag_id is not None and processed_tag_id is not None and ai:
                    ai_docs = await paperless.get_documents_needing_ai(
                        has_tag_id=processed_tag_id,
                        missing_tag_id=ai_processed_tag_id,
                    )
                    if ai_docs:
                        logger.info("Found %d documents needing AI-only reprocessing", len(ai_docs))
                        for i, doc in enumerate(ai_docs):
                            logger.info("[AI %d/%d] Running AI analysis for document %d", i + 1, len(ai_docs), doc["id"])
                            try:
                                ocr_text = doc.get("content", "")
                                if not ocr_text.strip():
                                    logger.warning("Skipping doc %d: no content available for AI analysis", doc["id"])
                                    continue
                                analysis = await ai.analyze(ocr_text)
                                if analysis:
                                    await _apply_ai_metadata(doc["id"], analysis, paperless, db)
                                    await db.upsert_document(
                                        paperless_id=doc["id"],
                                        title=doc.get("title", ""),
                                        status="done",
                                    )
                                else:
                                    logger.warning("AI analysis returned no metadata for doc %d", doc["id"])
                            except Exception as e:
                                logger.error(
                                    "AI reprocessing failed for document %d: %s", doc["id"], e, exc_info=True
                                )
                                await db.log_event("ERROR", "ai_reprocess_failed", doc["id"], str(e)[:200])

            finally:
                await paperless.close()
                if ocr:
                    await ocr.close()
                if ai:
                    await ai.close()

            logger.info("Scan cycle complete")
        finally:
            _scan_in_progress = False


# ── FastAPI app ──────────────────────────────────────────────────────────

config = Config.from_env()
_db: Database | None = None
_scheduler: AsyncIOScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db, _scheduler

    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )
    logger.info("Starting OCR Sidecar")
    logger.info("Paperless URL: %s", config.paperless_url)
    logger.info("OCR API URL: %s", config.ocr_api_url)
    logger.info("OCR Model: %s", config.ocr_model)
    logger.info("AI Analysis: %s", "enabled" if config.ai_analysis_enabled else "disabled")
    logger.info("Scan interval: %s", config.scan_interval)

    _db = Database(config)
    await _db.initialize()

    _scheduler = AsyncIOScheduler()
    job_kwargs = {"args": [config, _db], "replace_existing": True, "id": "scan"}
    parts = config.scan_interval.split()
    if len(parts) == 5:
        job_kwargs["trigger"] = "cron"
        job_kwargs["minute"] = parts[0]
        job_kwargs["hour"] = parts[1]
        job_kwargs["day"] = parts[2]
        job_kwargs["month"] = parts[3]
        job_kwargs["day_of_week"] = parts[4]
    else:
        job_kwargs["trigger"] = "interval"
        job_kwargs["minutes"] = int(parts[0]) if parts else 5

    _scheduler.add_job(scan, **job_kwargs)
    _scheduler.start()
    logger.info("Scheduler started")

    asyncio.create_task(scan(config, _db))

    yield

    if _scheduler:
        _scheduler.shutdown(wait=False)
    if _db:
        await _db.close()
    logger.info("Shutdown complete")


app = FastAPI(title="OCR Sidecar", lifespan=lifespan)


# ── API routes ───────────────────────────────────────────────────────────

@app.get("/")
async def index():
    try:
        from pathlib import Path
        p = Path("/app/static/index.html")
        if p.exists():
            return HTMLResponse(p.read_text())
    except Exception:
        pass
    return HTMLResponse("<html><body><h1>OCR Sidecar</h1><p>UI not found</p></body></html>")


@app.get("/api/config")
async def get_config():
    return {
        "paperless_url": config.paperless_url,
    }


@app.get("/api/stats")
async def get_stats():
    if not _db:
        raise HTTPException(503, "Database not ready")
    stats = await _db.get_stats()
    total = 0
    processed = 0
    paperless_total = 0
    try:
        paperless = PaperlessClient(config)
        try:
            docs = await paperless.get_all_documents()
            paperless_total = len(docs)
            stats["total"] = max(stats["total"], paperless_total)
        finally:
            await paperless.close()
    except Exception:
        pass
    return stats


@app.get("/api/documents")
async def list_documents():
    if not _db:
        raise HTTPException(503, "Database not ready")
    local_docs = await _db.get_all_documents()
    local_map = {d["paperless_id"]: d for d in local_docs}

    paperless = PaperlessClient(config)
    try:
        all_docs = await paperless.get_all_documents()
    finally:
        await paperless.close()

    result = []
    for d in all_docs:
        pid = d["id"]
        local = local_map.get(pid, {})
        result.append({
            "paperless_id": pid,
            "title": d.get("title", ""),
            "page_count": d.get("page_count", 0),
            "status": local.get("status", "pending"),
            "ocr_chars": local.get("ocr_chars", 0),
            "error": local.get("error"),
            "updated_at": str(local.get("updated_at", "")),
        })

    result.sort(key=lambda x: x["paperless_id"])
    return result


@app.get("/api/documents/{doc_id}")
async def get_document(doc_id: int):
    if not _db:
        raise HTTPException(503, "Database not ready")
    paperless = PaperlessClient(config)
    try:
        doc = await paperless.get_document(doc_id)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(404, "Document not found")
        raise HTTPException(502, f"Paperless error: {e.response.text[:200]}")
    finally:
        await paperless.close()

    local = await _db.get_document(doc_id)
    doc["sidecar_status"] = local.get("status", "unknown") if local else "unknown"
    doc["ocr_text_preview"] = (local.get("ocr_text", "") or "")[:2000] if local else None
    return doc


@app.post("/api/documents/{doc_id}/reprocess")
async def reprocess_document(doc_id: int):
    if not _db:
        raise HTTPException(503, "Database not ready")
    paperless = PaperlessClient(config)
    try:
        doc = await paperless.get_document(doc_id)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(404, "Document not found")
        raise HTTPException(502, f"Paperless error: {e.response.text[:200]}")
    finally:
        await paperless.close()

    await _db.upsert_document(
        paperless_id=doc_id,
        title=doc.get("title", ""),
        status="pending",
    )
    await _db.log_event("INFO", "reprocess_queued", doc_id, "Queued for reprocess")

    asyncio.create_task(_run_single(doc_id, doc))
    return {"status": "queued", "doc_id": doc_id}


async def _run_single(doc_id: int, doc: dict):
    global _db, config
    paperless = PaperlessClient(config)
    ocr = OcrProcessor(config)
    ai = AiAnalyzer(config) if config.ai_analysis_enabled else None
    try:
        processed_tag_id = await paperless.ensure_tag(config.processed_tag) if config.processed_tag else None
        status = await process_document(doc, paperless, ocr, ai, _db, config, processed_tag_id)
    except Exception as e:
        logger.error("Reprocess failed for doc %d: %s", doc_id, e)
        await _db.upsert_document(paperless_id=doc_id, status="failed", error=str(e)[:500])
        await _db.log_event("ERROR", "reprocess_failed", doc_id, str(e)[:200])
    finally:
        await paperless.close()
        await ocr.close()
        if ai:
            await ai.close()


async def _run_ai_only(doc_id: int, doc: dict, ocr_text: str):
    """Run only AI analysis on a document (skips OCR)."""
    global _db, config
    paperless = PaperlessClient(config)
    ai = AiAnalyzer(config)
    try:
        processed_tag_id = await paperless.ensure_tag(config.processed_tag) if config.processed_tag else None
        analysis = await ai.analyze(ocr_text)
        if analysis:
            await _apply_ai_metadata(doc_id, analysis, paperless, _db)
            await _db.upsert_document(
                paperless_id=doc_id,
                title=doc.get("title", ""),
                status="done",
                metadata_json=json.dumps(analysis, default=str),
            )
            logger.info("AI-only reprocess succeeded for doc %d", doc_id)
        else:
            logger.warning("AI analysis returned no metadata for doc %d", doc_id)
    except Exception as e:
        logger.error("AI-only reprocess failed for doc %d: %s", doc_id, e)
        await _db.upsert_document(paperless_id=doc_id, status="ai_failed", error=str(e)[:500])
        await _db.log_event("ERROR", "ai_reprocess_failed", doc_id, str(e)[:200])
    finally:
        await paperless.close()
        await ai.close()


async def _run_single_sema(doc_id: int, doc: dict):
    async with _reprocess_semaphore:
        await _run_single(doc_id, doc)


async def _run_ai_only_sema(doc_id: int, doc: dict, ocr_text: str):
    async with _reprocess_semaphore:
        await _run_ai_only(doc_id, doc, ocr_text)


@app.post("/api/documents/reprocess-batch")
async def reprocess_batch(body: dict):
    doc_ids = body.get("doc_ids", [])
    if not doc_ids or not isinstance(doc_ids, list):
        raise HTTPException(400, "doc_ids must be a non-empty list")
    if not _db:
        raise HTTPException(503, "Database not ready")

    paperless = PaperlessClient(config)
    try:
        for did in doc_ids:
            try:
                doc = await paperless.get_document(did)
                await _db.upsert_document(
                    paperless_id=did,
                    title=doc.get("title", ""),
                    status="pending",
                )
                await _db.log_event("INFO", "reprocess_queued", did, "Queued for reprocess")
                _reprocessing_tasks.append(asyncio.create_task(_run_single_sema(did, doc)))
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    logger.warning("Doc %d not found, skipping", did)
                else:
                    raise
    finally:
        await paperless.close()

    return {"status": "queued", "doc_ids": doc_ids, "count": len(doc_ids)}


@app.post("/api/reprocess-ocr-failed")
async def reprocess_ocr_failed():
    """Re-OCR documents that failed OCR (status = 'ocr_failed')."""
    global _db, config
    if not _db:
        raise HTTPException(503, "Database not ready")

    failed_docs = await _db.get_documents_needing_ocr()
    if not failed_docs:
        return {"status": "nothing_to_do", "count": 0}

    paperless = PaperlessClient(config)
    processed = []
    try:
        for rec in failed_docs:
            did = rec["paperless_id"]
            try:
                doc = await paperless.get_document(did)
                await _db.upsert_document(
                    paperless_id=did,
                    title=doc.get("title", ""),
                    status="pending",
                )
                await _db.log_event("INFO", "ocr_reprocess_queued", did, "Queued for OCR reprocess")
                _reprocessing_tasks.append(asyncio.create_task(_run_single_sema(did, doc)))
                processed.append(did)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    logger.warning("Doc %d not found, clearing ocr_failed status", did)
                    await _db.upsert_document(paperless_id=did, status="done")
                else:
                    raise
    finally:
        await paperless.close()

    return {"status": "queued", "doc_ids": processed, "count": len(processed)}


@app.post("/api/reprocess-ai-missing")
async def reprocess_ai_missing():
    """Re-run AI analysis on documents with valid OCR but missing AI metadata."""
    global _db, config
    if not _db:
        raise HTTPException(503, "Database not ready")
    if not config.ai_analysis_enabled:
        raise HTTPException(503, "AI analysis is disabled")

    ai_docs = await _db.get_documents_needing_ai()
    if not ai_docs:
        return {"status": "nothing_to_do", "count": 0}

    paperless = PaperlessClient(config)
    processed = []
    try:
        for rec in ai_docs:
            did = rec["paperless_id"]
            ocr_text = rec.get("ocr_text", "")
            if not ocr_text:
                try:
                    doc = await paperless.get_document(did)
                    ocr_text = doc.get("content", "")
                except Exception:
                    continue
            if not ocr_text.strip():
                logger.warning("Skipping doc %d: no OCR text available", did)
                continue

            try:
                doc = await paperless.get_document(did)
                _reprocessing_tasks.append(asyncio.create_task(_run_ai_only_sema(did, doc, ocr_text)))
                processed.append(did)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    logger.warning("Doc %d not found, skipping", did)
                else:
                    raise
    finally:
        await paperless.close()

    return {"status": "queued", "doc_ids": processed, "count": len(processed)}


@app.post("/api/reprocess-all")
async def reprocess_all():
    global _db, config
    if not _db:
        raise HTTPException(503, "Database not ready")

    asyncio.create_task(scan(config, _db))
    return {"status": "scan_triggered"}


@app.get("/api/logs")
async def get_logs(limit: int = 50, since: int = 0):
    if not _db:
        raise HTTPException(503, "Database not ready")
    if since:
        logs = await _db.get_recent_logs(limit=limit, since_id=since)
    else:
        logs = await _db.get_recent_logs(limit=limit)
    return logs


# ── Playground ──────────────────────────────────────────────────────────

PLAYGROUND_PROMPT_DEFAULT = """You are a document analyst. Analyze the document below and return:
1. A concise summary
2. Key dates, amounts, and parties
3. Suggested tags (up to 5, comma-separated)
4. Document type classification

Format your response as:

## Summary
...

## Key Details
- ...

## Suggested Tags
tag1, tag2, tag3

## Document Type
..."""


@app.get("/api/playground/models")
async def playground_models():
    models = []
    if config.ocr_model:
        models.append(config.ocr_model)
    if config.ai_model and config.ai_model != config.ocr_model:
        models.append(config.ai_model)
    return {
        "models": models or ["ocr"],
        "default": config.ocr_model or config.ai_model or "ocr",
        "api_url": config.ai_api_url or config.ocr_api_url,
    }


@app.post("/api/playground/prompt")
async def playground_prompt(body: dict):
    doc_id = body.get("doc_id")
    if not doc_id:
        raise HTTPException(400, "doc_id is required")

    custom_prompt = body.get("prompt", PLAYGROUND_PROMPT_DEFAULT)
    model = body.get("model") or config.ai_model or config.ocr_model
    temperature = body.get("temperature", 0.0)
    max_tokens = body.get("max_tokens", 4096)

    if not _db:
        raise HTTPException(503, "Database not ready")

    paperless = PaperlessClient(config)
    try:
        doc = await paperless.get_document(doc_id)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise HTTPException(404, "Document not found")
        raise HTTPException(502, f"Paperless error: {e.response.text[:200]}")
    finally:
        await paperless.close()

    local = await _db.get_document(doc_id)
    text = (local.get("ocr_text") if local else None) or doc.get("content", "")
    title = doc.get("title", f"doc_{doc_id}")

    if not text.strip():
        raise HTTPException(400, f"Document {doc_id} has no content or OCR text")

    api_url = config.ai_api_url or config.ocr_api_url
    api_key = config.ai_api_key or config.ocr_api_key

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    llm_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": custom_prompt},
            {
                "role": "user",
                "content": f"## Document: {title}\n\n{text}",
            },
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(180.0, connect=15.0)
        ) as client:
            resp = await client.post(api_url, json=llm_body, headers=headers)
            resp.raise_for_status()
            result = resp.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            502, f"LLM error: {e.response.status_code} - {e.response.text[:500]}"
        )
    except httpx.RequestError as e:
        raise HTTPException(502, f"LLM request failed: {e}")

    content = (
        result.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )

    parsed = AiAnalyzer._parse_json(content) if content else None

    return {
        "content": content,
        "parsed": parsed,
        "model": model,
        "usage": result.get("usage", {}),
    }


@app.get("/health")
async def health():
    return {"status": "ok"}

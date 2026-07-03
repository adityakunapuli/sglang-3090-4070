from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from app.config import Config

logger = logging.getLogger("ocr.db")


class Database:
    def __init__(self, config: Config) -> None:
        db_path = Path(config.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()

    def _init_tables(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paperless_id INTEGER UNIQUE NOT NULL,
                title TEXT DEFAULT '',
                pages INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                ocr_text TEXT,
                ocr_chars INTEGER DEFAULT 0,
                metadata_json TEXT,
                error TEXT,
                page_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS scan_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                level TEXT DEFAULT 'INFO',
                event TEXT,
                doc_id INTEGER,
                message TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_documents_paperless_id
                ON documents(paperless_id);
            CREATE INDEX IF NOT EXISTS idx_documents_status
                ON documents(status);
            CREATE INDEX IF NOT EXISTS idx_scan_log_timestamp
                ON scan_log(timestamp);
            """
        )
        self._conn.commit()

    async def initialize(self) -> None:
        await asyncio.get_event_loop().run_in_executor(None, self._init_tables)
        logger.info("Database initialized")

    async def close(self) -> None:
        self._conn.close()

    async def log_event(
        self, level: str, event: str, doc_id: int | None = None, message: str = ""
    ) -> None:
        async with self._lock:

            def _write() -> None:
                self._conn.execute(
                    "INSERT INTO scan_log (level, event, doc_id, message) VALUES (?, ?, ?, ?)",
                    (level, event, doc_id, message),
                )
                self._conn.commit()

            await asyncio.get_event_loop().run_in_executor(None, _write)

    async def upsert_document(
        self,
        paperless_id: int,
        title: str = "",
        pages: int = 0,
        status: str = "pending",
        ocr_text: str | None = None,
        metadata_json: str | None = None,
        error: str | None = None,
        page_count: int = 0,
    ) -> None:
        async with self._lock:

            def _upsert() -> None:
                ocr_chars = len(ocr_text) if ocr_text else 0
                self._conn.execute(
                    """INSERT INTO documents
                       (paperless_id, title, pages, status, ocr_text, ocr_chars,
                        metadata_json, error, page_count, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(paperless_id) DO UPDATE SET
                       title=excluded.title, pages=excluded.pages,
                       status=excluded.status, ocr_text=excluded.ocr_text,
                       ocr_chars=excluded.ocr_chars,
                       metadata_json=excluded.metadata_json,
                       error=excluded.error, page_count=excluded.page_count,
                       updated_at=CURRENT_TIMESTAMP""",
                    (
                        paperless_id,
                        title,
                        pages,
                        status,
                        ocr_text,
                        ocr_chars,
                        metadata_json,
                        error,
                        page_count,
                    ),
                )
                self._conn.commit()

            await asyncio.get_event_loop().run_in_executor(None, _upsert)

    async def get_document(
        self, paperless_id: int
    ) -> dict[str, Any] | None:
        def _get() -> dict | None:
            row = self._conn.execute(
                "SELECT * FROM documents WHERE paperless_id = ?",
                (paperless_id,),
            ).fetchone()
            return dict(row) if row else None

        return await asyncio.get_event_loop().run_in_executor(None, _get)

    async def get_all_documents(self) -> list[dict[str, Any]]:
        def _get_all() -> list[dict]:
            rows = self._conn.execute(
                "SELECT * FROM documents ORDER BY paperless_id"
            ).fetchall()
            return [dict(r) for r in rows]

        return await asyncio.get_event_loop().run_in_executor(None, _get_all)

    async def get_stats(self) -> dict[str, int]:
        def _stats() -> dict:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM documents"
            ).fetchone()[0]
            processed = self._conn.execute(
                "SELECT COUNT(*) FROM documents WHERE status IN ('done','ocr_done','ai_done')"
            ).fetchone()[0]
            pending = self._conn.execute(
                "SELECT COUNT(*) FROM documents WHERE status = 'pending'"
            ).fetchone()[0]
            failed = self._conn.execute(
                "SELECT COUNT(*) FROM documents WHERE status = 'failed'"
            ).fetchone()[0]
            ocr_done = self._conn.execute(
                "SELECT COUNT(*) FROM documents WHERE status IN ('ocr_done','ai_done','done')"
            ).fetchone()[0]
            ai_done = self._conn.execute(
                "SELECT COUNT(*) FROM documents WHERE status IN ('ai_done','done')"
            ).fetchone()[0]
            return {
                "total": total,
                "processed": processed,
                "pending": pending,
                "failed": failed,
                "ocr_done": ocr_done,
                "ai_done": ai_done,
            }

        return await asyncio.get_event_loop().run_in_executor(None, _stats)

    async def get_recent_logs(
        self, limit: int = 50, since_id: int | None = None
    ) -> list[dict[str, Any]]:
        def _logs() -> list[dict]:
            if since_id:
                rows = self._conn.execute(
                    "SELECT * FROM scan_log WHERE id > ? ORDER BY id DESC LIMIT ?",
                    (since_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM scan_log ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

        return await asyncio.get_event_loop().run_in_executor(None, _logs)

    async def get_last_log_id(self) -> int:
        def _last() -> int:
            row = self._conn.execute(
                "SELECT MAX(id) FROM scan_log"
            ).fetchone()
            return row[0] or 0

        return await asyncio.get_event_loop().run_in_executor(None, _last)

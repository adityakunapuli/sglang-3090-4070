from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Config

logger = logging.getLogger("ocr.paperless")


class PaperlessClient:
    def __init__(self, config: Config) -> None:
        self.base_url = config.paperless_url.rstrip("/")
        self.token = config.paperless_api_token
        self.headers = {
            "Authorization": f"Token {self.token}",
            "Accept": "application/json",
        }
        self._client = httpx.AsyncClient(
            headers=self.headers, timeout=httpx.Timeout(60.0)
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def ensure_tag(self, name: str) -> int:
        existing = await self.get_tag_id(name)
        if existing:
            return existing
        resp = await self._client.post(
            f"{self.base_url}/api/tags/",
            json={"name": name, "is_insensitive": True},
        )
        resp.raise_for_status()
        tag_id = resp.json()["id"]
        logger.info("Created tag '%s' (id=%d)", name, tag_id)
        return tag_id

    async def get_tag_id(self, name: str) -> int | None:
        resp = await self._client.get(
            f"{self.base_url}/api/tags/", params={"name__iexact": name}
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("count", 0) > 0:
            return data["results"][0]["id"]
        return None

    async def get_all_tags(self) -> dict[str, int]:
        tags: dict[str, int] = {}
        page = 1
        while True:
            resp = await self._client.get(
                f"{self.base_url}/api/tags/", params={"page": page, "page_size": 100}
            )
            resp.raise_for_status()
            data = resp.json()
            for t in data.get("results", []):
                tags[t["name"].lower()] = t["id"]
            if not data.get("next"):
                break
            page += 1
        return tags

    async def resolve_tag(self, name: str, existing_tags: dict[str, int]) -> int:
        key = name.lower().strip()
        if key in existing_tags:
            return existing_tags[key]
        tag_id = await self.ensure_tag(name)
        existing_tags[key] = tag_id
        return tag_id

    async def get_correspondent_id(self, name: str) -> int | None:
        resp = await self._client.get(
            f"{self.base_url}/api/correspondents/",
            params={"name__iexact": name},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("count", 0) > 0:
            return data["results"][0]["id"]
        return None

    async def create_correspondent(self, name: str) -> int:
        resp = await self._client.post(
            f"{self.base_url}/api/correspondents/",
            json={"name": name},
        )
        resp.raise_for_status()
        cid = resp.json()["id"]
        logger.info("Created correspondent '%s' (id=%d)", name, cid)
        return cid

    async def resolve_correspondent(
        self, name: str, cache: dict[str, int]
    ) -> int | None:
        if not name or not name.strip():
            return None
        key = name.lower().strip()
        if key in cache:
            return cache[key]
        existing = await self.get_correspondent_id(name)
        if existing:
            cache[key] = existing
            return existing
        cid = await self.create_correspondent(name)
        cache[key] = cid
        return cid

    async def get_document_type_id(self, name: str) -> int | None:
        resp = await self._client.get(
            f"{self.base_url}/api/document_types/",
            params={"name__iexact": name},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("count", 0) > 0:
            return data["results"][0]["id"]
        return None

    async def create_document_type(self, name: str) -> int:
        resp = await self._client.post(
            f"{self.base_url}/api/document_types/",
            json={"name": name},
        )
        resp.raise_for_status()
        dtid = resp.json()["id"]
        logger.info("Created document type '%s' (id=%d)", name, dtid)
        return dtid

    async def resolve_document_type(
        self, name: str, cache: dict[str, int]
    ) -> int | None:
        if not name or not name.strip():
            return None
        key = name.lower().strip()
        if key in cache:
            return cache[key]
        existing = await self.get_document_type_id(name)
        if existing:
            cache[key] = existing
            return existing
        dtid = await self.create_document_type(name)
        cache[key] = dtid
        return dtid

    async def get_custom_fields(self) -> dict[str, int]:
        fields: dict[str, int] = {}
        resp = await self._client.get(f"{self.base_url}/api/custom_fields/")
        resp.raise_for_status()
        for f in resp.json().get("results", []):
            fields[f["name"]] = f["id"]
        return fields

    async def get_all_documents(self, skip_tag_id: int | None = None) -> list[dict[str, Any]]:
        page = 1
        docs: list[dict[str, Any]] = []
        while True:
            params: dict = {"page": page, "page_size": 100, "ordering": "created"}
            resp = await self._client.get(
                f"{self.base_url}/api/documents/", params=params
            )
            resp.raise_for_status()
            data = resp.json()
            for doc in data.get("results", []):
                if skip_tag_id is None or skip_tag_id not in doc.get("tags", []):
                    docs.append(doc)
            if not data.get("next"):
                break
            page += 1
        logger.info("Fetched %d documents from Paperless-ngx", len(docs))
        return docs

    async def get_documents_needing_ai(
        self, has_tag_id: int, missing_tag_id: int
    ) -> list[dict[str, Any]]:
        """Fetch documents that have one tag but are missing another (e.g. ocr-processed but no ai-processed)."""
        page = 1
        docs: list[dict[str, Any]] = []
        while True:
            params: dict = {"page": page, "page_size": 100, "ordering": "created"}
            resp = await self._client.get(
                f"{self.base_url}/api/documents/", params=params
            )
            resp.raise_for_status()
            data = resp.json()
            for doc in data.get("results", []):
                tags = doc.get("tags", [])
                if has_tag_id in tags and missing_tag_id not in tags:
                    docs.append(doc)
            if not data.get("next"):
                break
            page += 1
        logger.info("Fetched %d documents needing AI reprocessing", len(docs))
        return docs

    async def get_document(self, doc_id: int) -> dict[str, Any]:
        resp = await self._client.get(
            f"{self.base_url}/api/documents/{doc_id}/"
        )
        resp.raise_for_status()
        return resp.json()

    async def download_document(self, doc_id: int) -> bytes:
        resp = await self._client.get(
            f"{self.base_url}/api/documents/{doc_id}/download/"
        )
        resp.raise_for_status()
        return resp.content

    async def update_content(self, doc_id: int, content: str) -> None:
        resp = await self._client.patch(
            f"{self.base_url}/api/documents/{doc_id}/",
            json={"content": content},
        )
        resp.raise_for_status()
        logger.info("Content updated for doc %d (%d chars)", doc_id, len(content))

    async def update_metadata(
        self,
        doc_id: int,
        title: str | None = None,
        correspondent: int | None = None,
        document_type: int | None = None,
        tags: list[int] | None = None,
        custom_fields: list[dict] | None = None,
    ) -> None:
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if correspondent is not None:
            body["correspondent"] = correspondent
        if document_type is not None:
            body["document_type"] = document_type
        if tags is not None:
            current = await self.get_document(doc_id)
            existing_tags = current.get("tags", [])
            body["tags"] = list(set(existing_tags + tags))
        if custom_fields is not None:
            body["custom_fields"] = custom_fields

        if not body:
            return

        resp = await self._client.patch(
            f"{self.base_url}/api/documents/{doc_id}/",
            json=body,
        )
        resp.raise_for_status()
        fields_log = ", ".join(body.keys())
        logger.info("Metadata updated for doc %d: %s", doc_id, fields_log)

    async def add_tag(self, doc_id: int, tag_id: int) -> None:
        doc = await self.get_document(doc_id)
        current_tags = doc.get("tags", [])
        if tag_id in current_tags:
            return
        resp = await self._client.patch(
            f"{self.base_url}/api/documents/{doc_id}/",
            json={"tags": current_tags + [tag_id]},
        )
        resp.raise_for_status()
        logger.info("Added tag_id=%d to doc %d", tag_id, doc_id)

    async def get_existing_content(self, doc_id: int) -> str:
        doc = await self.get_document(doc_id)
        return doc.get("content", "")

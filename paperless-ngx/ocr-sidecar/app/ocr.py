from __future__ import annotations

import asyncio
import base64
import io
import logging
import time
from typing import Any

import httpx
import fitz  # PyMuPDF

from app.config import Config

logger = logging.getLogger("ocr.processor")


def pdf_to_page_images(pdf_bytes: bytes, max_pages: int = 50) -> list[bytes]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total = min(len(doc), max_pages)
    images: list[bytes] = []
    for i in range(total):
        page = doc.load_page(i)
        pix = page.get_pixmap(dpi=200)
        img_bytes = pix.tobytes("png")
        images.append(img_bytes)
        logger.debug("Rendered page %d/%d (%d bytes)", i + 1, total, len(img_bytes))
    doc.close()
    logger.info("Rendered %d pages from PDF", len(images))
    return images


def image_to_data_url(img_bytes: bytes) -> str:
    b64 = base64.b64encode(img_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"


class OcrProcessor:
    def __init__(self, config: Config) -> None:
        self.api_url = config.ocr_api_url
        self.api_key = config.ocr_api_key
        self.model = config.ocr_model
        self.prompt = config.ocr_prompt
        self.max_pages = config.max_pages
        self.max_tokens = config.max_tokens_per_page
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(180.0, connect=15.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def ocr_page(self, image_bytes: bytes, page_num: int, total: int) -> str:
        data_url = image_to_data_url(image_bytes)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": self.prompt},
                    ],
                }
            ],
            "temperature": 0.0,
            "max_tokens": self.max_tokens,
            "stream": False,
        }

        for attempt in range(3):
            try:
                start = time.time()
                resp = await self._client.post(
                    self.api_url, json=body, headers=headers
                )
                elapsed = time.time() - start
                resp.raise_for_status()
                result = resp.json()
                text = (
                    result.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                logger.info(
                    "Page %d/%d OCR'd in %.1fs (%d chars)",
                    page_num, total, elapsed, len(text),
                )
                return text
            except httpx.HTTPStatusError as e:
                logger.warning(
                    "Page %d attempt %d failed: HTTP %d - %s",
                    page_num, attempt + 1, e.response.status_code,
                    e.response.text[:200],
                )
                if attempt < 2:
                    await asyncio.sleep(2.0 ** attempt)
                else:
                    raise
            except httpx.RequestError as e:
                logger.warning(
                    "Page %d attempt %d failed: %s",
                    page_num, attempt + 1, e,
                )
                if attempt < 2:
                    await asyncio.sleep(2.0 ** attempt)
                else:
                    raise

        return ""

    async def ocr_document(
        self, pdf_bytes: bytes, doc_id: int
    ) -> str | None:
        try:
            page_images = pdf_to_page_images(pdf_bytes, self.max_pages)
        except Exception as e:
            logger.error("Failed to render PDF for doc %d: %s", doc_id, e)
            return None

        if not page_images:
            logger.warning("No pages rendered for doc %d", doc_id)
            return None

        page_texts: list[str] = []
        for i, img in enumerate(page_images):
            try:
                text = await self.ocr_page(img, i + 1, len(page_images))
                page_texts.append(text)
            except Exception as e:
                logger.error(
                    "Failed to OCR page %d/%d for doc %d: %s",
                    i + 1, len(page_images), doc_id, e,
                )
                page_texts.append(f"\n[OCR failed for page {i+1}]\n")

        return "\n\n--- Page Break ---\n\n".join(page_texts)

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

        # Retry loop: hard errors get 3 attempts, QoS 503s get extended backoff.
        max_hard_attempts = 3
        hard_attempts = 0
        delay = 5.0  # Start at 5s for QoS backoff

        while True:
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
                if e.response.status_code == 503:
                    # QoS "model busy" — retry with exponential backoff, no attempt limit.
                    body_text = e.response.text[:200]
                    logger.info(
                        "Page %d/%d QoS busy (attempt #%d, backing off %.0fs): %s",
                        page_num, total, hard_attempts + 1, delay, body_text,
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 300)  # Cap at 5 minutes
                else:
                    hard_attempts += 1
                    logger.warning(
                        "Page %d attempt %d failed: HTTP %d - %s",
                        page_num, hard_attempts, e.response.status_code,
                        e.response.text[:200],
                    )
                    if hard_attempts >= max_hard_attempts:
                        raise
                    await asyncio.sleep(2.0 ** (hard_attempts - 1))
            except httpx.RequestError as e:
                hard_attempts += 1
                logger.warning(
                    "Page %d attempt %d failed: %s",
                    page_num, hard_attempts, e,
                )
                if hard_attempts >= max_hard_attempts:
                    raise
                await asyncio.sleep(2.0 ** (hard_attempts - 1))

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

        # Process pages concurrently with a per-document semaphore (max 4 pages at once)
        semaphore = asyncio.Semaphore(4)

        async def ocr_page_with_semaphore(img: bytes, page_num: int, total: int) -> tuple[int, str | None]:
            async with semaphore:
                try:
                    text = await self.ocr_page(img, page_num, total)
                    return page_num, text
                except Exception as e:
                    logger.error(
                        "Failed to OCR page %d/%d for doc %d: %s",
                        page_num, total, doc_id, e,
                    )
                    return page_num, None

        # Launch all pages concurrently
        tasks = [
            ocr_page_with_semaphore(img, i + 1, len(page_images))
            for i, img in enumerate(page_images)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        # Sort results by page number to preserve semantic order
        results.sort(key=lambda x: x[0])
        page_texts = [text for _, text in results if text is not None]
        failed_pages = [page_num for page_num, text in results if text is None]

        if failed_pages and not page_texts:
            # Every page failed — nothing useful to write back; let caller handle.
            logger.error(
                "All pages failed for doc %d (pages %s)", doc_id, failed_pages,
            )
            return None

        if failed_pages:
            # Partial success — insert markers but still return the good text.
            logger.warning(
                "Partial OCR for doc %d: %d/%d pages failed (%s)",
                doc_id, len(failed_pages), len(page_images), failed_pages,
            )
            return "\n\n--- Page Break ---\n\n".join(page_texts)

        logger.info("Completed OCR for doc %d: %d pages processed", doc_id, len(page_texts))
        return "\n\n--- Page Break ---\n\n".join(page_texts)

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import httpx

from app.config import Config

logger = logging.getLogger("ocr.ai")


class AiAnalyzer:
    def __init__(self, config: Config) -> None:
        self.api_url = config.ai_api_url
        self.api_key = config.ai_api_key or config.ocr_api_key
        self.model = config.ai_model
        self.prompt = config.analysis_prompt
        self.max_tokens = config.max_analysis_tokens
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(180.0, connect=15.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def analyze(self, doc_text: str) -> dict[str, Any] | None:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.prompt},
                {"role": "user", "content": f"Document text:\n\n{doc_text}"},
            ],
            "temperature": 0.0,
            "max_tokens": self.max_tokens,
            "stream": False,
        }

        max_hard_attempts = 3
        hard_attempts = 0
        delay = 5.0
        unparseable_count = 0

        while True:
            try:
                start = time.time()
                resp = await self._client.post(
                    self.api_url, json=body, headers=headers
                )
                elapsed = time.time() - start
                resp.raise_for_status()
                result = resp.json()
                content = (
                    result.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
                logger.info("AI analysis completed in %.1fs (%d chars)", elapsed, len(content))

                parsed = self._parse_json(content)
                if parsed:
                    return parsed
                unparseable_count += 1
                logger.warning("AI returned unparseable output (attempt %d)", unparseable_count)
                if unparseable_count >= 3:
                    return None
                await asyncio.sleep(2.0)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 503:
                    logger.info(
                        "AI analysis QoS busy (backing off %.0fs): %s",
                        delay, e.response.text[:200],
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 300)
                else:
                    hard_attempts += 1
                    logger.warning(
                        "AI analysis attempt %d failed: HTTP %d - %s",
                        hard_attempts, e.response.status_code, e.response.text[:200],
                    )
                    if hard_attempts >= max_hard_attempts:
                        raise
                    await asyncio.sleep(2.0 ** (hard_attempts - 1))
            except httpx.RequestError as e:
                hard_attempts += 1
                logger.warning(
                    "AI analysis attempt %d failed: %s", hard_attempts, e,
                )
                if hard_attempts >= max_hard_attempts:
                    raise
                await asyncio.sleep(2.0 ** (hard_attempts - 1))

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any] | None:
        content = content.strip()
        json_match = re.search(
            r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL
        )
        if json_match:
            content = json_match.group(1).strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        brace_start = content.find("{")
        brace_end = content.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            try:
                return json.loads(content[brace_start : brace_end + 1])
            except json.JSONDecodeError:
                pass
        return None

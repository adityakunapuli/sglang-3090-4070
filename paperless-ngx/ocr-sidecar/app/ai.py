from __future__ import annotations

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

        for attempt in range(3):
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
                logger.warning("AI returned unparseable output, retrying")
            except httpx.HTTPStatusError as e:
                logger.warning(
                    "AI analysis attempt %d failed: HTTP %d - %s",
                    attempt + 1, e.response.status_code, e.response.text[:200],
                )
                if attempt < 2:
                    import asyncio
                    await asyncio.sleep(2.0 ** attempt)
                else:
                    raise
            except httpx.RequestError as e:
                logger.warning(
                    "AI analysis attempt %d failed: %s", attempt + 1, e,
                )
                if attempt < 2:
                    import asyncio
                    await asyncio.sleep(2.0 ** attempt)
                else:
                    raise

        return None

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

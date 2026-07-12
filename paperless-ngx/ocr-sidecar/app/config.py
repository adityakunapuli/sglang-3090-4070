from __future__ import annotations

import os
from dataclasses import dataclass, field


_prompt_file = os.getenv("ANALYSIS_PROMPT_FILE", "/app/prompts/analysis.md")
if os.path.exists(_prompt_file):
    with open(_prompt_file) as f:
        ANALYSIS_PROMPT_DEFAULT = f.read().strip()
else:
    ANALYSIS_PROMPT_DEFAULT = """You are a document metadata extractor. Analyze the document text below and return a JSON object with exactly these fields:

{
  "title": "Concise, meaningful document title (max 10 words, no addresses)",
  "correspondent": "Sender or institution name (shortest recognizable form, no addresses)",
  "tags": ["up to 4 relevant category tags, keep them generic and reusable"],
  "document_date": "YYYY-MM-DD format date from the document; use the most relevant date (service date, statement date, etc.)",
  "document_type": "Precise classification (e.g. Invoice, Medical Consent Form, Insurance EOB, Lab Report, Contract)",
  "AI_Summary": "Rich markdown summary with sections: ## Document Overview (2-3 sentences), ## Key Points (bullets with bold labels), ## Important Dates and Figures (bullets), ## Action Items (numbered list if applicable)",
  "AI_KeyDetails": "Detailed markdown table(s) capturing ALL key fields: document type, parties, dates, amounts, reference numbers"
}

Rules:
- For tags, use only common categories (Medical, Invoice, Insurance, Legal, Tax YYYY, etc.)
- For AI_Summary, use markdown headings (##), bold (**text**), and bullet lists
- For AI_KeyDetails, use | Field | Value | markdown tables

Return ONLY valid JSON. No markdown code fences, no commentary before or after."""


@dataclass
class Config:
    paperless_url: str = field(
        default_factory=lambda: os.getenv(
            "PAPERLESS_URL", "http://paperless_web:8000"
        )
    )
    paperless_api_token: str = field(
        default_factory=lambda: os.getenv("PAPERLESS_API_TOKEN", "")
    )

    ocr_api_url: str = field(
        default_factory=lambda: os.getenv(
            "OCR_API_URL", "http://gateway:4000/v1/chat/completions"
        )
    )
    ocr_api_key: str = field(
        default_factory=lambda: os.getenv("OCR_API_KEY", "")
    )
    ocr_model: str = field(
        default_factory=lambda: os.getenv("OCR_MODEL", "ocr")
    )
    ocr_prompt: str = field(
        default_factory=lambda: os.getenv(
            "OCR_PROMPT",
            "Text Recognition:",
        )
    )

    scan_interval: str = field(
        default_factory=lambda: os.getenv("SCAN_INTERVAL", "*/5 * * * *")
    )
    processed_tag: str = field(
        default_factory=lambda: os.getenv("PROCESSED_TAG", "ocr-processed")
    )
    skip_tag: str = field(
        default_factory=lambda: os.getenv("SKIP_TAG", "ocr-processed")
    )
    max_pages: int = field(
        default_factory=lambda: int(os.getenv("MAX_PAGES", "50"))
    )
    max_tokens_per_page: int = field(
        default_factory=lambda: int(os.getenv("MAX_TOKENS_PER_PAGE", "8192"))
    )

    ai_analysis_enabled: bool = field(
        default_factory=lambda: os.getenv("AI_ANALYSIS_ENABLED", "yes").lower()
        in ("yes", "true", "1")
    )
    ai_model: str = field(
        default_factory=lambda: os.getenv("AI_MODEL", "ocr")
    )
    ai_api_url: str = field(
        default_factory=lambda: os.getenv(
            "AI_API_URL", "http://gateway:4000/v1/chat/completions"
        )
    )
    ai_api_key: str = field(
        default_factory=lambda: os.getenv("AI_API_KEY", "")
    )
    analysis_prompt: str = field(
        default_factory=lambda: os.getenv(
            "ANALYSIS_PROMPT", ANALYSIS_PROMPT_DEFAULT
        )
    )
    max_analysis_tokens: int = field(
        default_factory=lambda: int(os.getenv("MAX_ANALYSIS_TOKENS", "4096"))
    )

    only_doc_ids: set[int] = field(
        default_factory=lambda: {
            int(x) for x in os.getenv("ONLY_DOC_IDS", "").split(",") if x.strip().isdigit()
        }
    )

    log_level: str = field(
        default_factory=lambda: os.getenv("LOG_LEVEL", "INFO")
    )
    ui_port: int = field(
        default_factory=lambda: int(os.getenv("UI_PORT", "8002"))
    )
    db_path: str = field(
        default_factory=lambda: os.getenv("DB_PATH", "/app/data/sidecar.db")
    )

    @classmethod
    def from_env(cls) -> Config:
        return cls()

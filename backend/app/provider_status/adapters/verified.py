"""Statuspage-compatible feeds with selectors verified against the providers' component lists."""

import re
from typing import Any

from app.provider_status.adapters.base import BaseAdapter
from app.provider_status.adapters.statuspage import (
    latest_update,
    parse_statuspage_with_report,
    raw_component_names,
)
from app.provider_status.adapters.text import html_to_text
from app.provider_status.adapters.types import ParseResult

MISTRAL_API = re.compile(
    r"\b(?:completion|embedding|ocr|agents|conversations|fine-tuning|audio|integrations|"
    r"files|batch|workflows?|prompts|responses|skills) APIs?\b",
    re.IGNORECASE,
)
GROQ_API = re.compile(
    r"\bAPIs?\b|\bmodel performance or availability issue\b|"
    r"\b(?:(?:meta-llama/)?llama-[\w.-]+|openai/gpt-oss-[\w.-]+|whisper-large-v3(?:-turbo)?|"
    r"minimaxai/minimax-m2\.7|qwen/qwen3\.8-27b|canopy labs orpheus(?: arabic saudi| english)?)\b",
    re.IGNORECASE,
)


class VerifiedStatuspageAdapter(BaseAdapter):
    source = "statuspage_json"

    def __init__(self, provider: str, host: str, api_pattern: re.Pattern[str]) -> None:
        self.provider = provider
        self.url_prefix = f"https://{host}/"
        self.url = f"{self.url_prefix}api/v2/incidents.json"
        self.api_pattern = api_pattern

    def _affects_api(self, incident: dict[str, Any]) -> bool:
        components = raw_component_names(incident)
        if components:
            return any(self.api_pattern.fullmatch(component) for component in components)
        # These feeds omit components on incidents. Use named services in the public report.
        name = incident.get("name")
        title = html_to_text(name) if isinstance(name, str) else ""
        return bool(
            self.api_pattern.search(title) or self.api_pattern.search(latest_update(incident) or "")
        )

    def parse_with_report(self, body: bytes) -> ParseResult:
        return parse_statuspage_with_report(
            body,
            provider=self.provider,
            url_prefix=self.url_prefix,
            api_affected=self._affects_api,
            source=self.source,
        )


mistral = VerifiedStatuspageAdapter("mistral", "status.mistral.ai", MISTRAL_API)
groq = VerifiedStatuspageAdapter("groq", "groqstatus.com", GROQ_API)

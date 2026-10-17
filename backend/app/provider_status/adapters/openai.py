"""OpenAI's status page (incident.io): the JSON feed, and the RSS history as a fallback. Pure.

The incident.io JSON has no components, so `api_affected` reads the title and the newest
update for the API product names (the API component names of OpenAI's `summary.json`).
"""

import re
from typing import Any

from app.provider_status.adapters.base import BaseAdapter
from app.provider_status.adapters.openai_rss import parse_history_rss
from app.provider_status.adapters.statuspage import latest_update, parse_statuspage_with_report
from app.provider_status.adapters.text import html_to_text
from app.provider_status.adapters.types import ParseResult

API_PATTERN = re.compile(
    r"\bAPI\b|Chat Completions|Responses API|Embeddings|Batch|Realtime|Agents?\b"
    r"|Fine-tuning|Moderations|Files\b"
)


def mentions_api(text: str) -> bool:
    return API_PATTERN.search(text) is not None


def touches_api(incident: dict[str, Any]) -> bool:
    """True when the title or the newest update names an API product."""
    name = incident.get("name")
    title = html_to_text(name) if isinstance(name, str) else ""
    return mentions_api(title) or mentions_api(latest_update(incident) or "")


class OpenAIAdapter(BaseAdapter):
    provider = "openai"
    source = "incidentio_json"
    url = "https://status.openai.com/api/v2/incidents.json"
    url_prefix = "https://status.openai.com/"
    fallback_url = "https://status.openai.com/history.rss"
    fallback_source = "incidentio_rss"

    def parse_with_report(self, body: bytes) -> ParseResult:
        return parse_statuspage_with_report(
            body,
            provider=self.provider,
            url_prefix=self.url_prefix,
            api_affected=touches_api,
            source=self.source,
        )

    def parse_fallback_with_report(self, body: bytes) -> ParseResult:
        return parse_history_rss(body, url_prefix=self.url_prefix, mentions_api=mentions_api)


openai = OpenAIAdapter()

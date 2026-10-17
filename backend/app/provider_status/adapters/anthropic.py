"""Anthropic's status page (Statuspage v2 JSON at status.claude.com). Pure.

`status.anthropic.com` answers 301 to `status.claude.com`; the adapter pins the final host
because the poller does not follow redirects.
"""

from typing import Any

from app.provider_status.adapters.base import BaseAdapter
from app.provider_status.adapters.statuspage import (
    parse_statuspage_with_report,
    raw_component_names,
)
from app.provider_status.adapters.types import ParseResult


def touches_api(incident: dict[str, Any]) -> bool:
    """True when a component name contains `API` (case-sensitive): today the Claude API."""
    return any("API" in name for name in raw_component_names(incident))


class AnthropicAdapter(BaseAdapter):
    provider = "anthropic"
    source = "statuspage_json"
    url = "https://status.claude.com/api/v2/incidents.json"
    url_prefix = "https://status.claude.com/"

    def parse_with_report(self, body: bytes) -> ParseResult:
        return parse_statuspage_with_report(
            body,
            provider=self.provider,
            url_prefix=self.url_prefix,
            api_affected=touches_api,
            source=self.source,
        )


anthropic = AnthropicAdapter()

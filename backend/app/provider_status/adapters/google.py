"""Google Cloud's status page (`incidents.json`), filtered to Vertex AI and Gemini. Pure.

The feed is a JSON array of every Google Cloud incident. Only those whose `affected_products[]`
title names `Vertex AI` or `Gemini` are kept; every kept incident touches the API by
construction. `uri` is relative to the status site; `end` is null while the incident is open.
"""

from typing import Any
from urllib.parse import urlsplit

from app.provider_status.adapters.base import BaseAdapter
from app.provider_status.adapters.fields import (
    checked_url,
    collect,
    component_names,
    loads_json,
    optional_str,
    optional_time,
    plain_id,
    require_object,
    require_str,
    title_text,
    update_text,
)
from app.provider_status.adapters.types import FeedParseError, ParsedIncident, ParseResult

PRODUCT_MARKERS = ("Vertex AI", "Gemini")
IMPACTS = {
    "SERVICE_OUTAGE": "critical",
    "SERVICE_DISRUPTION": "major",
    "SERVICE_INFORMATION": "minor",
}


def _product_titles(incident: dict[str, Any]) -> list[str]:
    products = incident.get("affected_products") or []
    if not isinstance(products, list):
        raise FeedParseError("'affected_products' is not a list")
    titles = [require_object(product).get("title") for product in products]
    return [title for title in titles if isinstance(title, str)]


def _incident_url(uri: str, url_prefix: str) -> str:
    """`uri` resolved against the status site; an absolute uri must already be on it."""
    absolute = bool(urlsplit(uri).scheme) or uri.startswith("//")
    return checked_url(uri if absolute else url_prefix + uri.lstrip("/"), url_prefix)


def _latest_update(incident: dict[str, Any]) -> str | None:
    newest = incident.get("most_recent_update")
    if isinstance(newest, dict):
        return update_text(optional_str(newest, "text"))
    return None


def _incident(raw: Any, url_prefix: str) -> ParsedIncident | None:
    incident = require_object(raw)
    products = _product_titles(incident)
    if not any(marker in title for title in products for marker in PRODUCT_MARKERS):
        return None
    url = _incident_url(require_str(incident, "uri"), url_prefix)
    started_at = optional_time(incident, "begin")
    if started_at is None:
        raise FeedParseError("incident has no 'begin'")
    resolved_at = optional_time(incident, "end")
    return ParsedIncident(
        external_id=plain_id(require_str(incident, "id")),
        title=title_text(require_str(incident, "external_desc")),
        impact=IMPACTS.get(str(incident.get("status_impact")), "unknown"),
        status="investigating" if resolved_at is None else "resolved",
        api_affected=True,
        components=component_names(products),
        started_at=started_at,
        resolved_at=resolved_at,
        url=url,
        latest_update=_latest_update(incident),
        feed_updated_at=optional_time(incident, "modified"),
    )


class GoogleAdapter(BaseAdapter):
    provider = "google"
    source = "gcp_json"
    url = "https://status.cloud.google.com/incidents.json"
    url_prefix = "https://status.cloud.google.com/"

    def parse_with_report(self, body: bytes) -> ParseResult:
        document = loads_json(body)
        if not isinstance(document, list):
            raise FeedParseError("google gcp_json: the document is not an incident list")
        try:
            return collect(document, lambda raw: _incident(raw, self.url_prefix))
        except FeedParseError as exc:
            raise FeedParseError(f"google gcp_json: {exc}") from exc


google = GoogleAdapter()

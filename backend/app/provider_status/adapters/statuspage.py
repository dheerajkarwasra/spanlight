"""Atlassian Statuspage v2 `incidents.json`, also served by incident.io. Pure.

Anthropic's page is Statuspage; OpenAI's is incident.io's compatible rendering of the same shape
(no `components`, no `started_at`). The document is `{page, incidents[]}`; an incident has `id`,
`name`, `status`, `impact`, `created_at`, `updated_at`, `resolved_at`, optionally `started_at`
and `components[{name}]`, and `incident_updates[{body, created_at}]`. Neither feed carries the
incident page's url in a usable form (Statuspage's `shortlink` is on another host), so it is
built from the id under the adapter's own prefix.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any

from app.provider_status.adapters.fields import (
    collect,
    component_names,
    incident_url,
    loads_json,
    normal_impact,
    normal_status,
    optional_time,
    require_object,
    require_str,
    title_text,
    update_text,
)
from app.provider_status.adapters.types import FeedParseError, ParsedIncident, ParseResult

ApiAffected = Callable[[dict[str, Any]], bool]


def _list(incident: dict[str, Any], key: str) -> list[Any]:
    value = incident.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise FeedParseError(f"{key!r} is not a list")
    return value


def raw_component_names(incident: dict[str, Any]) -> list[str]:
    """The `components[].name` strings as the feed sends them."""
    names = [require_object(component).get("name") for component in _list(incident, "components")]
    return [name for name in names if isinstance(name, str)]


def latest_update(incident: dict[str, Any]) -> str | None:
    """The newest `incident_updates[].body` (by `created_at`) as capped plain text."""
    newest: tuple[datetime, str | None] | None = None
    for raw in _list(incident, "incident_updates"):
        update = require_object(raw)
        created = optional_time(update, "created_at")
        body = update.get("body")
        if created is not None and (newest is None or created > newest[0]):
            newest = (created, body if isinstance(body, str) else None)
    return update_text(newest[1]) if newest else None


def _incident(raw: Any, url_prefix: str, api_affected: ApiAffected) -> ParsedIncident:
    incident = require_object(raw)
    external_id = require_str(incident, "id")
    url = incident_url(url_prefix, external_id)
    created_at = optional_time(incident, "created_at")
    started_at = optional_time(incident, "started_at") or created_at
    if started_at is None:
        raise FeedParseError("incident has neither started_at nor created_at")
    return ParsedIncident(
        external_id=external_id,
        title=title_text(require_str(incident, "name")),
        impact=normal_impact(incident.get("impact")),
        status=normal_status(incident.get("status")),
        api_affected=api_affected(incident),
        components=component_names(raw_component_names(incident)),
        started_at=started_at,
        resolved_at=optional_time(incident, "resolved_at"),
        url=url,
        latest_update=latest_update(incident),
        feed_updated_at=optional_time(incident, "updated_at"),
    )


def parse_statuspage_with_report(
    body: bytes, *, provider: str, url_prefix: str, api_affected: ApiAffected, source: str
) -> ParseResult:
    """The incidents of a Statuspage-shaped `body`, with what was skipped."""
    document = loads_json(body)
    incidents = document.get("incidents") if isinstance(document, dict) else None
    if not isinstance(incidents, list):
        raise FeedParseError(f"{provider} {source}: the document has no incident list")
    try:
        return collect(incidents, lambda raw: _incident(raw, url_prefix, api_affected))
    except FeedParseError as exc:
        raise FeedParseError(f"{provider} {source}: {exc}") from exc


def parse_statuspage(
    body: bytes, *, provider: str, url_prefix: str, api_affected: ApiAffected, source: str
) -> list[ParsedIncident]:
    """The incidents of a Statuspage-shaped `body`."""
    incidents, _ = parse_statuspage_with_report(
        body, provider=provider, url_prefix=url_prefix, api_affected=api_affected, source=source
    )
    return incidents

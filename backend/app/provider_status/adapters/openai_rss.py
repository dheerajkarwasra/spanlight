"""incident.io's RSS history (`status.openai.com/history.rss`), the OpenAI fallback. Pure.

RSS 2.0 parsed with `defusedxml`, which refuses entity definitions (no billion laughs) and
external references. An item is the incident's newest update: `guid` and `link` are
`https://status.openai.com//incidents/<id>`, `pubDate` is RFC 822, and `description` is HTML:
`<b>Status: Resolved</b><br/>` then the update text, then optionally
`<b>Affected components</b><ul><li>Name (Component status)</li></ul>`. There is no impact.

`started_at` is `pubDate` (the feed has no start time), and a resolved item's
`resolved_at` is that same moment, so a resolved row never looks open-ended.
"""

import re
from collections.abc import Callable
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

from app.provider_status.adapters.fields import (
    aware,
    checked_url,
    collect,
    component_names,
    incident_url,
    normal_status,
    title_text,
    update_text,
)
from app.provider_status.adapters.text import html_to_text
from app.provider_status.adapters.types import FeedParseError, ParsedIncident, ParseResult

if TYPE_CHECKING:
    from xml.etree.ElementTree import Element

# Every pattern here is linear: no two adjacent quantifiers can match the same character
# (the status word is taken with its spaces and stripped in code).
_STATUS = re.compile(r"<b>\s*Status:([^<]*)</b>", re.IGNORECASE)
_COMPONENTS_HEADING = re.compile(r"<b>\s*Affected components\s*</b>", re.IGNORECASE)
# The component's own status, which incident.io appends to its name in the list.
_COMPONENT_STATUSES = frozenset(
    {
        "operational",
        "degraded performance",
        "partial outage",
        "full outage",
        "major outage",
        "under maintenance",
    }
)
_ENDED = frozenset({"resolved", "postmortem"})
# A real description is well under 2 KB; the bound keeps every pass over it cheap whatever the
# feed sends (the poller allows a 2 MB body).
DESCRIPTION_LIMIT = 64 * 1024
# Lowercases A-Z only. `str.lower()` can change a string's length (U+0130 becomes two code
# points), which would shift every position found in the lowered copy.
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _root(body: bytes) -> "Element":
    try:
        return fromstring(body)
    except (DefusedXmlException, ParseError) as exc:
        raise FeedParseError(f"openai incidentio_rss: not RSS: {type(exc).__name__}") from exc


def _pub_date(item: "Element") -> datetime:
    value = item.findtext("pubDate")
    if not value:
        raise FeedParseError("RSS item has no pubDate")
    try:
        return aware(parsedate_to_datetime(value))
    except (TypeError, ValueError) as exc:
        raise FeedParseError("RSS pubDate is not an RFC 822 date") from exc


def _external_id(item: "Element", url_prefix: str) -> str:
    link = (item.findtext("link") or item.findtext("guid") or "").strip()
    checked_url(link, url_prefix)
    external_id = link.rstrip("/").rsplit("/", 1)[-1]
    incident_url(url_prefix, external_id)
    return external_id


def _description_parts(description: str) -> tuple[str, str | None, tuple[str, ...]]:
    """The status word, the update text and the component names of one item's description."""
    description = description[:DESCRIPTION_LIMIT]
    status_match = _STATUS.search(description)
    status = status_match.group(1).strip() if status_match else ""
    parts = _COMPONENTS_HEADING.split(description, maxsplit=1)
    head, tail = parts[0], parts[1] if len(parts) == 2 else ""
    return status, update_text(_STATUS.sub(" ", head)), _components(tail)


def _list_items(listing: str) -> list[str]:
    """The contents of each closed `<li>…</li>` in any case, found with `find` (linear)."""
    lowered = listing.translate(_ASCII_LOWER)
    items: list[str] = []
    start = lowered.find("<li>")
    while start >= 0:
        end = lowered.find("</li>", start + 4)
        if end < 0:
            break
        items.append(listing[start + 4 : end])
        start = lowered.find("<li>", end + 5)
    return items


def _component_name(raw: str) -> str:
    """The list item as text, without the `(Component status)` suffix incident.io appends."""
    text = html_to_text(raw)
    opening = text.rfind("(")
    if (
        opening >= 0
        and text.endswith(")")
        and text[opening + 1 : -1].lower() in _COMPONENT_STATUSES
    ):
        return text[:opening].rstrip()
    return text


def _components(listing: str) -> tuple[str, ...]:
    return component_names(_component_name(raw) for raw in _list_items(listing))


def _item(raw: Any, url_prefix: str, mentions_api: Callable[[str], bool]) -> ParsedIncident:
    item: Element = raw
    external_id = _external_id(item, url_prefix)
    published = _pub_date(item)
    title = title_text(item.findtext("title") or "")
    status_word, latest, components = _description_parts(item.findtext("description") or "")
    status = normal_status(status_word)
    affected = (
        any(mentions_api(name) for name in components)
        if components
        else mentions_api(title) or mentions_api(latest or "")
    )
    return ParsedIncident(
        external_id=external_id,
        title=title,
        impact="unknown",
        status=status,
        api_affected=affected,
        components=components,
        started_at=published,
        resolved_at=published if status in _ENDED else None,
        url=incident_url(url_prefix, external_id),
        latest_update=latest,
        feed_updated_at=published,
    )


def parse_history_rss(
    body: bytes, *, url_prefix: str, mentions_api: Callable[[str], bool]
) -> ParseResult:
    """The incidents of an incident.io RSS history `body`, with what was skipped."""
    root = _root(body)
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise FeedParseError("openai incidentio_rss: the document is not an RSS channel")
    try:
        return collect(channel.findall("item"), lambda raw: _item(raw, url_prefix, mentions_api))
    except FeedParseError as exc:
        raise FeedParseError(f"openai incidentio_rss: {exc}") from exc

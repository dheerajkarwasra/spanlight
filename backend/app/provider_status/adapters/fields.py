"""Field readers and normalisation shared by the status feed adapters. Pure.

A structural problem (a body that is not JSON, a missing id, a timestamp that is not a date, more
than `MAX_INCIDENTS_PER_FEED` incidents) raises `FeedParseError`: the whole feed is suspect.
Three problems are local to one incident and only skip it, counted in the `ParseReport`: a
timestamp without a timezone (`NaiveTimestampError`), a link outside the adapter's own status
page (`ForeignUrlError`) and an item that cannot be stored as it is (`InvalidIncidentError`).
"""

import json
import re
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any, NoReturn

from app.provider_status.adapters.text import clamp, html_to_text
from app.provider_status.adapters.types import (
    FeedParseError,
    ParsedIncident,
    ParseReport,
    ParseResult,
)

TITLE_LIMIT = 500
COMPONENT_LIMIT = 120
MAX_COMPONENTS = 20
UPDATE_LIMIT = 2000
# About ten times what the real feeds list. A feed with more is a broken or compromised page, and
# keeping it would put that many rows into a table every tenant reads.
MAX_INCIDENTS_PER_FEED = 500

STATUSES = frozenset({"investigating", "identified", "monitoring", "resolved", "postmortem"})
IMPACTS = frozenset({"none", "minor", "major", "critical"})

# An incident id becomes one path segment of the stored url, so it must be one.
_ID_SEGMENT = re.compile(r"[A-Za-z0-9_-]{1,64}")
# A stored url is printable ASCII without spaces, quotes (", ', `), angle brackets or backslashes.
_URL_CHARACTERS = re.compile(r"[!#-&(-;=?-\[\]-_a-~]+")


class NaiveTimestampError(Exception):
    """The incident carries a timestamp without a timezone; it is skipped, not guessed."""


class ForeignUrlError(Exception):
    """The incident's link is not on the adapter's own status page; it is skipped."""


class InvalidIncidentError(Exception):
    """The incident cannot be stored as it is (an empty title, an unusable id); it is skipped."""


def _reject_constant(name: str) -> NoReturn:
    raise ValueError(f"{name} is not valid JSON")


def loads_json(body: bytes) -> Any:
    """The JSON document in `body`, parsed strictly (no NaN or infinity)."""
    try:
        return json.loads(body, parse_constant=_reject_constant)
    except (ValueError, RecursionError) as exc:
        raise FeedParseError(f"body is not JSON: {type(exc).__name__}") from exc


def require_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FeedParseError(f"expected an object, got {type(value).__name__}")
    return value


def require_str(source: dict[str, Any], key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value:
        raise FeedParseError(f"missing or non-string {key!r}")
    return value


def optional_str(source: dict[str, Any], key: str) -> str | None:
    value = source.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise FeedParseError(f"non-string {key!r}")
    return value


def aware(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise NaiveTimestampError
    return moment


def optional_time(source: dict[str, Any], key: str) -> datetime | None:
    """The ISO 8601 timestamp at `key`, `None` when absent; naive raises `NaiveTimestampError`."""
    value = optional_str(source, key)
    if value is None:
        return None
    try:
        return aware(datetime.fromisoformat(value))
    except ValueError as exc:
        raise FeedParseError(f"{key!r} is not an ISO 8601 timestamp") from exc


def checked_url(candidate: str, prefix: str) -> str:
    """`candidate` when it is a plain url under `prefix`, else `ForeignUrlError`."""
    if not candidate.startswith(prefix) or not _URL_CHARACTERS.fullmatch(candidate):
        raise ForeignUrlError
    return candidate


def plain_id(external_id: str) -> str:
    """`external_id` when it is one plain path segment, else `InvalidIncidentError`."""
    if not _ID_SEGMENT.fullmatch(external_id):
        raise InvalidIncidentError
    return external_id


def incident_url(prefix: str, external_id: str) -> str:
    """The incident page on the status site, `<prefix>incidents/<id>`."""
    if not _ID_SEGMENT.fullmatch(external_id):
        raise ForeignUrlError
    return f"{prefix}incidents/{external_id}"


def _vocabulary_word(value: Any) -> str:
    return value.strip().lower() if isinstance(value, str) else ""


def normal_status(value: Any) -> str:
    """A Statuspage/incident.io status, or `unknown` for anything outside that vocabulary."""
    status = _vocabulary_word(value)
    return status if status in STATUSES else "unknown"


def normal_impact(value: Any) -> str:
    """A Statuspage/incident.io impact, or `unknown` for anything outside that vocabulary."""
    impact = _vocabulary_word(value)
    return impact if impact in IMPACTS else "unknown"


def title_text(value: str) -> str:
    """The title as capped plain text; a title that is empty once stripped skips the incident."""
    title = clamp(html_to_text(value), TITLE_LIMIT)
    if not title:
        raise InvalidIncidentError
    return title


def update_text(value: str | None) -> str | None:
    """An update body as capped plain text, `None` when there is no text."""
    text = clamp(html_to_text(value), UPDATE_LIMIT) if value else ""
    return text or None


def component_names(names: Iterable[str]) -> tuple[str, ...]:
    """Distinct non-empty names as capped plain text, in feed order, at most `MAX_COMPONENTS`."""
    kept: list[str] = []
    for name in names:
        text = clamp(html_to_text(name), COMPONENT_LIMIT)
        if text and text not in kept:
            kept.append(text)
        if len(kept) == MAX_COMPONENTS:
            break
    return tuple(kept)


def collect(items: Iterable[Any], build: Callable[[Any], ParsedIncident | None]) -> ParseResult:
    """`build` over `items`: `None` drops an item silently, a skip is counted in the report.

    More than `MAX_INCIDENTS_PER_FEED` kept incidents raises `FeedParseError`: the whole feed is
    refused, so nothing of it is stored.
    """
    kept: list[ParsedIncident] = []
    skipped_naive = skipped_url = skipped_invalid = 0
    for item in items:
        try:
            incident = build(item)
        except NaiveTimestampError:
            skipped_naive += 1
            continue
        except ForeignUrlError:
            skipped_url += 1
            continue
        except InvalidIncidentError:
            skipped_invalid += 1
            continue
        if incident is not None:
            if len(kept) == MAX_INCIDENTS_PER_FEED:
                raise FeedParseError(f"more than {MAX_INCIDENTS_PER_FEED} incidents")
            kept.append(incident)
    return kept, ParseReport(
        kept=len(kept),
        skipped_naive=skipped_naive,
        skipped_url=skipped_url,
        skipped_invalid=skipped_invalid,
    )

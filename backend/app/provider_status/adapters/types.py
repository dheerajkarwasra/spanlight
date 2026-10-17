"""The shapes every status feed adapter shares. Pure: no I/O."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class FeedParseError(Exception):
    """The feed body is not the format its adapter expects (the "feed format changed" signal)."""


@dataclass(frozen=True, slots=True)
class ParsedIncident:
    """One incident as a status feed describes it, normalised and length-capped.

    Every datetime is timezone-aware: an incident with a naive timestamp is skipped, not guessed.
    `impact` is `none | minor | major | critical | unknown`; `status` is
    `investigating | identified | monitoring | resolved | postmortem | unknown`.
    """

    external_id: str
    title: str
    impact: str
    status: str
    api_affected: bool
    components: tuple[str, ...]
    started_at: datetime
    resolved_at: datetime | None
    url: str
    latest_update: str | None
    feed_updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class ParseReport:
    """What one parse kept and what it skipped, for the poller's log line.

    `skipped_naive`: a timestamp without a timezone. `skipped_url`: a link (or an id the link is
    built from) outside the adapter's status page. `skipped_invalid`: an item that cannot be
    stored as it is, such as a title that is empty once stripped or an id that is not one plain
    path segment.
    """

    kept: int
    skipped_naive: int
    skipped_url: int
    skipped_invalid: int = 0


ParseResult = tuple[list[ParsedIncident], ParseReport]


class FeedAdapter(Protocol):
    """One provider's status feed: where it lives and how to read it."""

    @property
    def provider(self) -> str: ...

    @property
    def source(self) -> str: ...

    @property
    def url(self) -> str: ...

    @property
    def url_prefix(self) -> str: ...

    @property
    def fallback_url(self) -> str | None: ...

    @property
    def fallback_source(self) -> str | None: ...

    def parse(self, body: bytes) -> list[ParsedIncident]: ...

    def parse_with_report(self, body: bytes) -> ParseResult: ...

    def parse_fallback(self, body: bytes) -> list[ParsedIncident]: ...

    def parse_fallback_with_report(self, body: bytes) -> ParseResult: ...

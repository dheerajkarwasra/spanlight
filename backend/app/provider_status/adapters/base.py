"""The common part of every adapter: `parse` from `parse_with_report`, and no fallback feed."""

from abc import ABC, abstractmethod

from app.provider_status.adapters.types import FeedParseError, ParsedIncident, ParseResult


class BaseAdapter(ABC):
    """A status feed adapter; subclasses set the class attributes and `parse_with_report`."""

    provider: str
    source: str
    url: str
    url_prefix: str
    fallback_url: str | None = None
    fallback_source: str | None = None

    @abstractmethod
    def parse_with_report(self, body: bytes) -> ParseResult:
        """The incidents in the primary feed's `body`, with what was skipped."""

    def parse(self, body: bytes) -> list[ParsedIncident]:
        return self.parse_with_report(body)[0]

    def parse_fallback_with_report(self, body: bytes) -> ParseResult:
        """The incidents in the fallback feed's `body`; this adapter has none."""
        raise FeedParseError(f"{self.provider} has no fallback feed")

    def parse_fallback(self, body: bytes) -> list[ParsedIncident]:
        return self.parse_fallback_with_report(body)[0]

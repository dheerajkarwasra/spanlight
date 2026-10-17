"""Status feed adapters: pure parsers, bytes in, `ParsedIncident`s out.

Each adapter pins its feed URL (a constant, never a setting) and the `https://status.<host>/`
prefix every stored incident url must start with. The poller fetches; nothing here does I/O.
"""

from app.provider_status.adapters.anthropic import anthropic
from app.provider_status.adapters.google import google
from app.provider_status.adapters.openai import openai
from app.provider_status.adapters.types import (
    FeedAdapter,
    FeedParseError,
    ParsedIncident,
    ParseReport,
    ParseResult,
)

ADAPTERS: tuple[FeedAdapter, ...] = (anthropic, openai, google)

__all__ = [
    "ADAPTERS",
    "FeedAdapter",
    "FeedParseError",
    "ParseReport",
    "ParseResult",
    "ParsedIncident",
]

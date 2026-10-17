"""The convention a span was exported under, and the fields a convention mapper produces."""

import enum
from dataclasses import dataclass, field
from typing import Any

from app.db.models.telemetry import SpanKind


class Convention(enum.StrEnum):
    GEN_AI = "gen_ai"
    OPENINFERENCE = "openinference"
    VERCEL_AI = "vercel_ai"
    TRACELOOP = "traceloop"
    NONE = "none"


@dataclass(frozen=True)
class MappedUsage:
    """Usage as the exporter sent it: counts are raw and fitted later, never defaulted to 0."""

    input_tokens: int | float | str | None = None
    output_tokens: int | float | str | None = None
    cached_tokens: int | float | str | None = None
    cache_write_tokens: int | float | str | None = None
    web_search_requests: int | float | str | None = None
    service_tier: str | None = None
    # The provider's spelling when `normalize_tier` did not know it (`usage.service_tier_raw`).
    service_tier_raw: str | None = None


@dataclass(frozen=True)
class MappedFields:
    """What one convention mapper read from a span; `None` means "not sent", never a guess."""

    provider: str | None = None
    model: str | None = None
    usage: MappedUsage = MappedUsage()
    input: Any = None
    output: Any = None
    kind: SpanKind = SpanKind.OTHER
    # The exporter's finer kind (`spanlight.operation`), e.g. `embedding` or `workflow`.
    operation: str | None = None
    finish_reason_raw: str | None = None
    # Raw as read; fitted to a number (or dropped and counted) when the native span is built.
    time_to_first_token_ms: Any = None
    status_message_fallback: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    # Keys the mapper read, removed from the stored attributes.
    consumed_keys: frozenset[str] = frozenset()
    trace_name: str | None = None
    # Embeddings report no output tokens; the mapper implies 0 and says so.
    output_tokens_implied: bool = False
    # Flattened messages past the index or nesting limits, dropped and counted.
    dropped_messages: int = 0

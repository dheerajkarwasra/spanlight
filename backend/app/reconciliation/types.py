"""The row shapes reconciliation passes between its pure modules and its shells. No I/O.

`MappedLine` is a `provider_cost_lines` row (§6.7) before the ids; `CostRollupRow` is a
`cost_rollups_daily` row (§6.8) minus `id` and `computed_at`; `UsageBucketResult` is one result
of a provider usage report after mapping (§7.4). `LineKind` and `TokenType` come from the ORM
model (`app.db.models.billing`), which spells the CHECK constraints of §6.7 once; they are
re-exported here so the pure modules and their tests have one import surface.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TypedDict, get_args
from uuid import UUID

from app.db.models.billing import LineKind, TokenType
from app.pricing.tiers import ServiceTier

__all__ = [
    "LINE_KINDS",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_DIMENSION_LENGTH",
    "MAX_MODEL_LENGTH",
    "MAX_PROVIDER_SCOPE_LENGTH",
    "MAX_QUANTITY",
    "TOKEN_TYPES",
    "CostRollupRow",
    "CsvRow",
    "LineKind",
    "MappedLine",
    "TokenType",
    "UsageBucketResult",
]

# One definition each, shared with the ORM model: `LINE_KINDS` and `TOKEN_TYPES` are the same
# sets as the `provider_cost_lines` CHECKs, for the CSV mapper's membership tests.
LINE_KINDS: frozenset[str] = frozenset(get_args(LineKind))
TOKEN_TYPES: frozenset[str] = frozenset(get_args(TokenType))

# Length limits of §6.7 / §8.4, enforced by the mappers so every writer stores the same shape.
MAX_MODEL_LENGTH = 256
MAX_DESCRIPTION_LENGTH = 512
MAX_PROVIDER_SCOPE_LENGTH = 128
# `provider_cost_lines.quantity` is a `bigint`: every writer refuses a larger count.
MAX_QUANTITY = 2**63 - 1
# `service_tier` and `context_window` as the provider reports them, before normalization; the
# known values are a few characters, the cap only bounds what a malformed page can store.
MAX_DIMENSION_LENGTH = 128


@dataclass(frozen=True)
class MappedLine:
    """One provider line for one UTC day, as the provider or a CSV reported it, after mapping."""

    day: date
    provider: str
    line_key: str
    line_kind: LineKind
    model_raw: str | None
    model_pattern: str | None
    token_type: TokenType | None
    service_tier: ServiceTier | None
    context_window: str | None
    provider_scope: str | None
    description: str
    quantity: int | None
    amount_usd: Decimal


@dataclass(frozen=True)
class CostRollupRow:
    """One `cost_rollups_daily` row: a project's spans for one day, provider, model and tier."""

    project_id: UUID
    day: date
    provider: str | None
    provider_canonical: str | None
    model: str | None
    model_pattern: str | None
    service_tier: ServiceTier | None
    calls: int
    priced_calls: int
    unpriced_calls: int
    usage_unreported_calls: int
    cut_stream_calls: int
    cache_hit_calls: int
    unmodelled_modifier_calls: int
    uncached_input_tokens: int
    cache_read_tokens: int
    cache_write_5m_tokens: int
    cache_write_1h_tokens: int
    output_tokens: int
    web_search_requests: int
    cost_usd: Decimal | None
    fee_usd: Decimal | None


@dataclass(frozen=True)
class UsageBucketResult:
    """One usage-report result for one UTC day, keyed as the cost lines are.

    A count the provider did not report is `None`, never 0: OpenAI reports no cache writes and
    no web searches, and a usage report not grouped by a dimension leaves it `None`.
    """

    day: date
    provider: str
    model: str | None
    service_tier: ServiceTier | None
    context_window: str | None
    provider_scope: str | None
    uncached_input: int | None
    cache_read: int | None
    cache_write_5m: int | None
    cache_write_1h: int | None
    output: int | None
    web_search_requests: int | None


class CsvRow(TypedDict):
    """One data row of the §8.4 upload, every cell as the text in the file (blank when absent)."""

    day: str
    provider: str
    line_kind: str
    amount_usd: str
    model: str
    token_type: str
    service_tier: str
    quantity: str
    description: str
    provider_scope: str

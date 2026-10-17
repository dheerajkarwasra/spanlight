"""The cost explanation of one span (spec §8.1): the lines behind its stored cost.

Money and rates are decimal strings, never floats. A rate keeps the scale it is stored with
(`"3.000000"`); a total has the eight decimals of `numeric(14, 8)`. A line's `usd` is unrounded,
so it is written with at least eight decimals and as many more as it really has; a zero line is
`"0"`, as the UI dims it.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, PlainSerializer

from app.api.schemas.common import Money
from app.pricing.explain import ExplanationStatus, LineKind
from app.pricing.tiers import ServiceTier, TierNote

_MONEY_QUANTUM = Decimal("0.00000001")


def _format_line_usd(value: Decimal) -> str:
    if value == 0:
        return "0"
    exact = value.normalize()
    exponent = exact.as_tuple().exponent
    # `normalize` drops trailing zeros; pad back to the eight decimals of a stored amount.
    if isinstance(exponent, int) and exponent > -8:
        exact = exact.quantize(_MONEY_QUANTUM)
    return format(exact, "f")


LineUsd = Annotated[Decimal, PlainSerializer(_format_line_usd, return_type=str, when_used="json")]


class CostPriceOut(BaseModel):
    """The price row that produced the stored cost, found by the span's `pricing_version`."""

    source: Literal["seed", "override"]
    version: str
    model_pattern: str
    effective_from: datetime
    min_prompt_tokens: int
    override_id: uuid.UUID | None


class CostUsageOut(BaseModel):
    """The span's usage as stored; null where it was not reported."""

    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    cache_write_tokens: int | None
    cache_write_1h_tokens: int | None
    web_search_requests: int | None
    reasoning_tokens: int | None
    unreported_reason: str | None
    """Why the output count is unknown (`usage.unreported_reason`); null when it is known."""


class OriginalUsageOut(BaseModel):
    """What the call billed when it filled the gateway cache (`spanlight.cache.original_usage`)."""

    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    cache_write_tokens: int | None
    cache_write_1h_tokens: int | None


class CostLineOut(BaseModel):
    kind: LineKind
    quantity: int
    unit: Literal["tokens", "requests"]
    rate: Money
    rate_unit: Literal["usd_per_mtok", "usd_per_1k_requests"]
    multiplier: Money
    usd: LineUsd


class OutputBreakdownOut(BaseModel):
    """Reasoning tokens are part of `output_tokens`; never a line of their own."""

    reasoning: int
    other: int


class CostExplanationOut(BaseModel):
    span_id: str
    trace_id: str
    status: ExplanationStatus
    provider: str | None
    provider_canonical: str | None
    model: str | None
    service_tier: ServiceTier | None
    tier_multiplier: Money
    tier_note: TierNote | None
    price: CostPriceOut | None
    """Null when the row that priced the span cannot be produced (see `status`)."""
    usage: CostUsageOut
    lines: list[CostLineOut]
    output_breakdown: OutputBreakdownOut | None
    recomputed_usd: Money | None
    stored_usd: Money | None
    stored_fee_usd: Money | None
    message: str | None
    """The sentence shown for every status but `ok`; on `ok`, unpriced searches when there are."""
    original_usage: OriginalUsageOut | None
    """Set on a gateway cache hit only: the usage the cached answer was billed for."""

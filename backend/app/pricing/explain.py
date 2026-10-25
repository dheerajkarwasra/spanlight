"""Cost explanation: the lines behind one span's stored cost. Pure: no I/O.

`explain_span` rebuilds a span's cost from the price row and fee rate that produced it, line
by line, and compares the sum with the figure ingestion stored. The classes come from
`app.pricing.price_row.token_classes`, the split `PriceBook.cost` prices, so the two cannot drift:

    reads    = min(cached or 0, input)
    writes   = min(cache_write or 0, input - reads)
    writes1h = min(cache_write_1h or 0, writes)
    writes5m = writes - writes1h
    uncached = input - reads - writes

Each token line is `quantity x rate x multiplier / 1_000_000`, a fee line `quantity x rate /
1_000`, both unrounded. A token line's multiplier is the tier's times every priced modifier's
(`TierMultipliers.factor`, as `PriceBook.cost` applies it); the message names the modifiers,
while `tier_multiplier` stays the tier's alone. The total is quantized once, half-up at eight
decimals, as the price book does. The explanation never re-prices with today's snapshot: the
caller passes the row that was in force when the span was priced, and a row that no longer
exists is reported as such with the stored figure, never replaced.

Status precedence: `usage_rollup`, then `cache_hit`, `no_usage`, `no_price`,
`price_version_missing`, `override_deleted`, then `ok` or `mismatch`. Token counts over
`MAX_PRICED_TOKENS` are `no_price`: ingestion left them unpriced. A span whose token cost
is unknown still shows its web search fee line when searches were counted and a rate exists:
unknown is never reported as zero, and a known fee is never dropped.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from app.pricing import explain_copy as copy
from app.pricing.cost import MAX_PRICED_TOKENS, Price
from app.pricing.explain_copy import UNREPORTED_REASON_COPY
from app.pricing.fees import FEE_QUANTUM, ServerToolFee
from app.pricing.price_row import token_classes
from app.pricing.tiers import TierMultipliers, TierNote

__all__ = [
    "UNREPORTED_REASON_COPY",
    "CostExplanation",
    "CostLine",
    "ExplanationStatus",
    "LineKind",
    "PriceLookupStatus",
    "SpanCostInputs",
    "explain_span",
]

PriceLookupStatus = Literal["found", "no_price", "version_missing", "override_deleted"]
ExplanationStatus = Literal[
    "ok",
    "mismatch",
    "no_usage",
    "no_price",
    "price_version_missing",
    "override_deleted",
    "cache_hit",
    "usage_rollup",
]
LineKind = Literal[
    "uncached_input", "cache_read", "cache_write_5m", "cache_write_1h", "output", "web_search"
]
LineUnit = Literal["tokens", "requests"]
RateUnit = Literal["usd_per_mtok", "usd_per_1k_requests"]

_PER_MILLION = Decimal(1_000_000)
_PER_THOUSAND = Decimal(1_000)
_COST_QUANTUM = FEE_QUANTUM  # numeric(14, 8), the one quantum of `PriceBook.cost`
_ONE = Decimal(1)
_ZERO = Decimal(0)


@dataclass(frozen=True)
class SpanCostInputs:
    """What the explanation needs from one span row, as stored."""

    provider: str | None
    model: str | None
    started_at: datetime
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    cache_write_tokens: int | None
    cache_write_1h_tokens: int | None
    web_search_requests: int | None
    service_tier: str | None
    reasoning_tokens: int | None
    usage_unreported_reason: str | None
    stored_cost_usd: Decimal | None
    stored_fee_usd: Decimal | None
    pricing_version: str | None
    is_cache_hit: bool
    usage_rollup: bool = False  # `spans.usage_rollup`: the span repeats a descendant's usage
    modifiers: tuple[str, ...] = ()  # `app.pricing.modifiers.reported_modifiers` of the span


@dataclass(frozen=True)
class CostLine:
    kind: LineKind
    quantity: int
    unit: LineUnit
    rate: Decimal
    rate_unit: RateUnit
    multiplier: Decimal
    usd: Decimal


@dataclass(frozen=True)
class CostExplanation:
    status: ExplanationStatus
    lines: tuple[CostLine, ...]
    recomputed_usd: Decimal | None
    stored_cost_usd: Decimal | None
    stored_fee_usd: Decimal | None
    tier_multiplier: Decimal
    tier_note: TierNote | None
    # (reasoning, other) when `reasoning_tokens` is known; never a line.
    output_breakdown: tuple[int, int] | None
    message: str | None


def explain_span(
    span: SpanCostInputs,
    price: Price | None,
    fee: ServerToolFee | None,
    multipliers: TierMultipliers,
    price_lookup_status: PriceLookupStatus,
) -> CostExplanation:
    """Explain `span`'s stored cost from the `price` row and `fee` rate that produced it.

    `price` is the row named by the span's `pricing_version` (None when the lookup failed, as
    `price_lookup_status` says); `fee` is the web search rate in force at the span's start.
    """
    multiplier = multipliers.factor(span.provider, span.service_tier, span.modifiers)
    fee_lines = _fee_lines(span, fee)
    tier_multiplier = multipliers.for_(span.provider, span.service_tier)
    frame = _Frame(span, tier_multiplier, multipliers.note(span.provider, span.service_tier))

    if span.usage_rollup:
        token_lines = _token_lines(span, price, multiplier)
        lines = token_lines + fee_lines
        recomputed = _total(lines) if token_lines else None
        return frame.build("usage_rollup", lines, recomputed, copy.USAGE_ROLLUP_MESSAGE)
    if span.is_cache_hit:
        lines = _zeroed(_token_lines(span, price, multiplier))
        zero = _ZERO.quantize(_COST_QUANTUM)
        return frame.build("cache_hit", lines, zero, copy.CACHE_HIT_MESSAGE)
    if span.input_tokens is None or span.output_tokens is None:
        message = copy.no_usage_message(
            span.usage_unreported_reason,
            input_missing=span.input_tokens is None,
            fee_priced=bool(fee_lines),
        )
        return frame.build("no_usage", fee_lines, None, message)
    if max(span.input_tokens, span.output_tokens) > MAX_PRICED_TOKENS:
        return frame.build("no_price", fee_lines, None, copy.IMPLAUSIBLE_USAGE_MESSAGE)
    if price is None or price_lookup_status != "found":
        return frame.build(*_lookup_failure(span, price_lookup_status, fee, fee_lines))

    lines = _token_lines(span, price, multiplier) + fee_lines
    recomputed = _total(lines)
    if recomputed == span.stored_cost_usd:
        priced = [(m, f) for m in span.modifiers if (f := multipliers.modifier(span.provider, m))]
        message = " ".join(
            part
            for part in (copy.modifiers_message(priced), _missing_fee_message(span, fee))
            if part is not None
        )
        return frame.build("ok", lines, recomputed, message or None)
    mismatch = copy.mismatch_message(recomputed, span.stored_cost_usd)
    return frame.build("mismatch", lines, recomputed, mismatch)


@dataclass(frozen=True)
class _Frame:
    """The fields every explanation of one span shares, whatever its status."""

    span: SpanCostInputs
    multiplier: Decimal
    note: TierNote | None

    def build(
        self,
        status: ExplanationStatus,
        lines: tuple[CostLine, ...],
        recomputed: Decimal | None,
        message: str | None,
    ) -> CostExplanation:
        return CostExplanation(
            status=status,
            lines=lines,
            recomputed_usd=recomputed,
            stored_cost_usd=self.span.stored_cost_usd,
            stored_fee_usd=self.span.stored_fee_usd,
            tier_multiplier=self.multiplier,
            tier_note=self.note,
            output_breakdown=_output_breakdown(self.span),
            message=message,
        )


def _lookup_failure(
    span: SpanCostInputs,
    price_lookup_status: PriceLookupStatus,
    fee: ServerToolFee | None,
    fee_lines: tuple[CostLine, ...],
) -> tuple[ExplanationStatus, tuple[CostLine, ...], None, str]:
    """What to show when the price row that priced the span cannot be produced.

    A missing version or a deleted override shows the stored figure and no lines. No price at
    ingest shows the fee line when one is priced: the token cost is unknown, the fee is not.
    """
    if price_lookup_status == "version_missing":
        return "price_version_missing", (), None, copy.version_missing_message(span.pricing_version)
    if price_lookup_status == "override_deleted":
        return "override_deleted", (), None, copy.override_deleted_message(span.pricing_version)
    if fee_lines:
        return "no_price", fee_lines, None, copy.FEE_ONLY_MESSAGE
    # The missing price is the main fact; unpriced searches are named after it.
    message = copy.no_price_message(span.provider, span.model)
    missing_fee = _missing_fee_message(span, fee)
    if missing_fee is not None:
        message = f"{message} {missing_fee}"
    return "no_price", (), None, message


def _token_lines(
    span: SpanCostInputs, price: Price | None, multiplier: Decimal
) -> tuple[CostLine, ...]:
    """The five token classes, in the order of spec §7.1; empty without a price or usage."""
    if price is None or span.input_tokens is None or span.output_tokens is None:
        return ()
    # The same split as `PriceBook.cost` (`price_row.token_classes`), so the two cannot drift.
    split = token_classes(
        span.input_tokens, span.cached_tokens, span.cache_write_tokens, span.cache_write_1h_tokens
    )
    classes: tuple[tuple[LineKind, int, Decimal], ...] = (
        ("uncached_input", split.uncached, price.input_per_mtok),
        ("cache_read", split.reads, price.cached_rate),
        ("cache_write_5m", split.writes_5m, price.write_5m_rate),
        ("cache_write_1h", split.writes_1h, price.write_1h_rate),
        ("output", span.output_tokens, price.output_per_mtok),
    )
    return tuple(
        CostLine(
            kind=kind,
            quantity=quantity,
            unit="tokens",
            rate=rate,
            rate_unit="usd_per_mtok",
            multiplier=multiplier,
            usd=quantity * rate * multiplier / _PER_MILLION,
        )
        for kind, quantity, rate in classes
    )


def _fee_lines(span: SpanCostInputs, fee: ServerToolFee | None) -> tuple[CostLine, ...]:
    """The web search line, only when a count was reported and a rate exists."""
    if span.web_search_requests is None or fee is None:
        return ()
    return (
        CostLine(
            kind="web_search",
            quantity=span.web_search_requests,
            unit="requests",
            rate=fee.usd_per_1k_requests,
            rate_unit="usd_per_1k_requests",
            multiplier=_ONE,
            usd=span.web_search_requests * fee.usd_per_1k_requests / _PER_THOUSAND,
        ),
    )


def _zeroed(lines: tuple[CostLine, ...]) -> tuple[CostLine, ...]:
    """The same lines at quantity 0: a cache hit costs nothing by definition."""
    return tuple(
        CostLine(
            kind=line.kind,
            quantity=0,
            unit=line.unit,
            rate=line.rate,
            rate_unit=line.rate_unit,
            multiplier=line.multiplier,
            usd=_ZERO,
        )
        for line in lines
    )


def _total(lines: tuple[CostLine, ...]) -> Decimal:
    """The sum of the lines, rounded once as `PriceBook.cost` rounds."""
    return sum((line.usd for line in lines), _ZERO).quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP)


def _output_breakdown(span: SpanCostInputs) -> tuple[int, int] | None:
    if span.reasoning_tokens is None or span.output_tokens is None:
        return None
    reasoning = min(span.reasoning_tokens, span.output_tokens)
    return reasoning, span.output_tokens - reasoning


def _missing_fee_message(span: SpanCostInputs, fee: ServerToolFee | None) -> str | None:
    """Searches were counted but no rate prices them; None when there is nothing to say."""
    if span.web_search_requests is None or fee is not None:
        return None
    return copy.missing_fee_message(span.provider, span.pricing_version)

"""Explain one stored span's cost: read the row, find the price that made it, build the answer.

The arithmetic is `app.pricing.explain.explain_span`; this module only feeds it. The span is read
in the request's transaction, which `require()` has bound to the project, so row-level security
hides every other project's spans as well as the explicit `project_id` filter does.

The price row is looked up by the span's own `pricing_version`, never re-priced with today's
rows. The lookup status is derived from what the lookup found, so a status and a price can never
disagree: a version no loaded row carries is `version_missing` (or `override_deleted` for an
`override:<id>` version), and a version that exists but has no row for this model counts as
missing too, since the row that priced the span is gone.

A `mismatch` is a pricing bug signal: it is counted and logged with the ids and both figures only,
never the span's payload.
"""

import uuid
from datetime import UTC
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.cost_explanation import (
    CostExplanationOut,
    CostLineOut,
    CostPriceOut,
    CostUsageOut,
    OriginalUsageOut,
    OutputBreakdownOut,
)
from app.core.observability import COST_EXPLANATION_MISMATCH
from app.db.models import Span
from app.pricing.cost import OVERRIDE_VERSION_PREFIX, Price, PriceBook, load_price_book
from app.pricing.explain import (
    UNREPORTED_REASON_COPY,
    CostExplanation,
    PriceLookupStatus,
    SpanCostInputs,
    explain_span,
)
from app.pricing.modifiers import reported_modifiers
from app.pricing.provider_alias import canonical_provider
from app.pricing.tiers import multipliers_for, normalize_tier

logger = structlog.get_logger(__name__)

_WEB_SEARCH = "web_search"
_ORIGINAL_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cached_tokens",
    "cache_write_tokens",
    "cache_write_1h_tokens",
)


async def explain_stored_span(
    db: AsyncSession, project_id: uuid.UUID, trace_id: str, span_id: str, *, org_id: uuid.UUID
) -> CostExplanationOut | None:
    """The explanation of one span of `project_id`, or None when the span is not visible.

    `org_id` selects the organization's price overrides, so an `override:<id>` version is found
    while the override exists.
    """
    row = await db.scalar(
        select(Span).where(
            Span.project_id == project_id,
            Span.trace_id == trace_id.lower(),
            Span.span_id == span_id.lower(),
        )
    )
    if row is None:
        return None
    book = await load_price_book(db, org_id)
    inputs = span_cost_inputs(row)
    price, lookup = lookup_price(book, inputs)
    fee = book.fees.find(inputs.provider, _WEB_SEARCH, inputs.started_at)
    multipliers = multipliers_for(inputs.pricing_version, inputs.started_at)
    explanation = explain_span(inputs, price, fee, multipliers, lookup)
    if explanation.status == "mismatch":
        _report_mismatch(project_id, row.span_id, explanation)
    return _out(row, inputs, price, explanation)


def span_cost_inputs(row: Span) -> SpanCostInputs:
    """What `explain_span` needs from the row and the usage attributes ingestion kept."""
    attributes: dict[str, Any] = row.attributes or {}
    return SpanCostInputs(
        provider=row.provider,
        model=row.model,
        started_at=row.started_at,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cached_tokens=row.cached_tokens,
        cache_write_tokens=row.cache_write_tokens,
        cache_write_1h_tokens=row.cache_write_1h_tokens,
        web_search_requests=row.web_search_requests,
        service_tier=row.service_tier,
        reasoning_tokens=_count(attributes.get("usage.reasoning_tokens")),
        usage_unreported_reason=_reason(attributes.get("usage.unreported_reason")),
        stored_cost_usd=row.cost_usd,
        stored_fee_usd=row.fee_usd,
        pricing_version=row.pricing_version,
        is_cache_hit=attributes.get("spanlight.cache") == "hit",
        usage_rollup=row.usage_rollup,
        modifiers=reported_modifiers(attributes),
    )


def lookup_price(book: PriceBook, span: SpanCostInputs) -> tuple[Price | None, PriceLookupStatus]:
    """The row named by the span's `pricing_version`, and what the lookup found."""
    version = span.pricing_version
    if version is None:
        return None, "no_price"
    if not book.has_version(version):
        if version.startswith(OVERRIDE_VERSION_PREFIX):
            return None, "override_deleted"
        return None, "version_missing"
    price = None
    if span.model is not None:
        price = book.find_by_version(
            version,
            span.provider,
            span.model,
            span.started_at,
            prompt_tokens=span.input_tokens or 0,
        )
    return (price, "found") if price is not None else (None, "version_missing")


def _count(value: object) -> int | None:
    """A token count from an attribute: a non-negative int, never a bool or a string."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _reason(value: object) -> str | None:
    """One of the documented `usage.unreported_reason` values; anything else is unknown."""
    return value if isinstance(value, str) and value in UNREPORTED_REASON_COPY else None


def _original_usage(row: Span, inputs: SpanCostInputs) -> OriginalUsageOut | None:
    """`spanlight.cache.original_usage` on a cache hit, when the gateway kept it."""
    stored = (row.attributes or {}).get("spanlight.cache.original_usage")
    if not inputs.is_cache_hit or not isinstance(stored, dict):
        return None
    return OriginalUsageOut(**{key: _count(stored.get(key)) for key in _ORIGINAL_USAGE_KEYS})


def _report_mismatch(project_id: uuid.UUID, span_id: str, explanation: CostExplanation) -> None:
    COST_EXPLANATION_MISMATCH.inc()
    stored = explanation.stored_cost_usd
    recomputed = explanation.recomputed_usd
    logger.warning(
        "cost_explanation_mismatch",
        project_id=str(project_id),
        span_id=span_id,
        stored=format(stored, "f") if stored is not None else None,
        recomputed=format(recomputed, "f") if recomputed is not None else None,
    )


def _price_out(price: Price | None) -> CostPriceOut | None:
    if price is None:
        return None
    override_id = None
    if price.source == "override":
        override_id = uuid.UUID(price.version.removeprefix(OVERRIDE_VERSION_PREFIX))
    return CostPriceOut(
        source=price.source,
        version=price.version,
        model_pattern=price.model_pattern,
        effective_from=price.effective_from.astimezone(UTC),
        min_prompt_tokens=price.min_prompt_tokens,
        override_id=override_id,
    )


def _usage_out(inputs: SpanCostInputs) -> CostUsageOut:
    return CostUsageOut(
        input_tokens=inputs.input_tokens,
        output_tokens=inputs.output_tokens,
        cached_tokens=inputs.cached_tokens,
        cache_write_tokens=inputs.cache_write_tokens,
        cache_write_1h_tokens=inputs.cache_write_1h_tokens,
        web_search_requests=inputs.web_search_requests,
        reasoning_tokens=inputs.reasoning_tokens,
        unreported_reason=inputs.usage_unreported_reason,
    )


def _out(
    row: Span, inputs: SpanCostInputs, price: Price | None, explanation: CostExplanation
) -> CostExplanationOut:
    breakdown = explanation.output_breakdown
    return CostExplanationOut(
        span_id=row.span_id,
        trace_id=row.trace_id,
        status=explanation.status,
        provider=row.provider,
        provider_canonical=canonical_provider(row.provider),
        model=row.model,
        # The column's CHECK holds the four canonical names, which normalize to themselves.
        service_tier=normalize_tier(row.provider, row.service_tier),
        tier_multiplier=explanation.tier_multiplier,
        tier_note=explanation.tier_note,
        price=_price_out(price),
        usage=_usage_out(inputs),
        lines=[CostLineOut(**vars(line)) for line in explanation.lines],
        output_breakdown=(
            OutputBreakdownOut(reasoning=breakdown[0], other=breakdown[1]) if breakdown else None
        ),
        recomputed_usd=explanation.recomputed_usd,
        stored_usd=explanation.stored_cost_usd,
        stored_fee_usd=explanation.stored_fee_usd,
        message=explanation.message,
        original_usage=_original_usage(row, inputs),
    )

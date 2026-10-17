"""The cost explanation of spec §7.1 (pure, no database).

Failure modes: a line that disagrees with the figure ingestion stored (the explanation must
reproduce `PriceBook.cost` exactly, pilot cases included); a tier multiplier applied to the
wrong lines; a fee that disappears when the token cost is unknown; a stored figure that is
re-priced instead of explained; an unknown reported as a zero.

The three pilot cases are Anthropic's own billed amounts for real calls made on 2026-10-14
(`docs/plans/reports/pilot-2026-10-14.md`, D1 and D2), exact to 1e-8.
"""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from app.pricing.cost import Price
from app.pricing.explain import (
    UNREPORTED_REASON_COPY,
    CostExplanation,
    CostLine,
    PriceLookupStatus,
    SpanCostInputs,
    explain_span,
)
from app.pricing.fees import ServerToolFee
from app.pricing.tiers import TierMultipliers

AT = datetime(2026, 10, 15, 12, tzinfo=UTC)
EFFECTIVE_FROM = datetime(2026, 10, 15, tzinfo=UTC)
VERSION = "2026-10-15"

TOKEN_KINDS = ("uncached_input", "cache_read", "cache_write_5m", "cache_write_1h", "output")

MULTIPLIERS = TierMultipliers(
    table={
        ("anthropic", "batch"): Decimal("0.5"),
        ("anthropic", "priority"): Decimal("1.0"),
        ("openai", "batch"): Decimal("0.5"),
        ("openai", "flex"): Decimal("0.5"),
        ("google", "batch"): Decimal("0.5"),
    },
    commitment_billed=frozenset({("anthropic", "priority")}),
)


def _price(
    model: str,
    input_rate: str,
    output_rate: str,
    cached_rate: str | None,
    *,
    provider: str = "anthropic",
    write_5m: str | None = None,
    write_1h: str | None = None,
    min_prompt_tokens: int = 0,
    version: str = VERSION,
) -> Price:
    return Price(
        provider=provider,
        model_pattern=model,
        input_per_mtok=Decimal(input_rate),
        output_per_mtok=Decimal(output_rate),
        cached_input_per_mtok=Decimal(cached_rate) if cached_rate is not None else None,
        effective_from=EFFECTIVE_FROM,
        version=version,
        source="override" if version.startswith("override:") else "seed",
        cache_write_5m_per_mtok=Decimal(write_5m) if write_5m is not None else None,
        cache_write_1h_per_mtok=Decimal(write_1h) if write_1h is not None else None,
        min_prompt_tokens=min_prompt_tokens,
    )


# The 2026-10-15 snapshot rows the tests price against.
HAIKU_45 = _price("claude-haiku-4-5", "1.00", "5.00", "0.10", write_5m="1.25", write_1h="2.00")
SONNET_46 = _price("claude-sonnet-4-6", "3.00", "15.00", "0.30", write_5m="3.75", write_1h="6.00")
HAIKU_55_LONG = _price(
    "claude-haiku-5-5",
    "0.50",
    "2.50",
    "0.05",
    write_5m="0.625",
    write_1h="1.00",
    min_prompt_tokens=100_001,
)
GPT_4O = _price("gpt-4o", "2.50", "10.00", "1.25", provider="openai")

WEB_SEARCH_FEE = ServerToolFee(
    provider="anthropic",
    tool="web_search",
    usd_per_1k_requests=Decimal("10.00"),
    effective_from=EFFECTIVE_FROM,
    version=VERSION,
)


def _span(**overrides: Any) -> SpanCostInputs:
    """The Haiku 4.5 five-minute pilot span, with `overrides` applied."""
    base = SpanCostInputs(
        provider="anthropic",
        model="claude-haiku-4-5",
        started_at=AT,
        input_tokens=6423,
        output_tokens=10,
        cached_tokens=None,
        cache_write_tokens=6404,
        cache_write_1h_tokens=None,
        web_search_requests=None,
        service_tier=None,
        reasoning_tokens=None,
        usage_unreported_reason=None,
        stored_cost_usd=Decimal("0.00807400"),
        stored_fee_usd=None,
        pricing_version=VERSION,
        is_cache_hit=False,
    )
    return replace(base, **overrides)


def _explain(
    span: SpanCostInputs,
    price: Price | None = HAIKU_45,
    fee: ServerToolFee | None = None,
    lookup: PriceLookupStatus = "found",
) -> CostExplanation:
    return explain_span(span, price, fee, MULTIPLIERS, lookup)


def _kinds(explanation: CostExplanation) -> tuple[str, ...]:
    return tuple(line.kind for line in explanation.lines)


def _line(explanation: CostExplanation, kind: str) -> CostLine:
    return next(line for line in explanation.lines if line.kind == kind)


# --- pilot cases ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("span", "price"),
    [
        pytest.param(_span(), HAIKU_45, id="haiku-4-5-5m-write"),
        pytest.param(
            _span(
                output_tokens=12, cache_write_1h_tokens=6404, stored_cost_usd=Decimal("0.01288700")
            ),
            HAIKU_45,
            id="haiku-4-5-1h-write",
        ),
        pytest.param(
            _span(
                model="claude-haiku-5-5",
                input_tokens=157_718,
                output_tokens=64,
                cache_write_tokens=None,
                stored_cost_usd=Decimal("0.07901900"),
            ),
            HAIKU_55_LONG,
            id="haiku-5-5-long-prompt",
        ),
    ],
)
def test_pilot_cases_explain_to_the_stored_cost(span: SpanCostInputs, price: Price) -> None:
    explanation = _explain(span, price)

    assert explanation.status == "ok"
    assert explanation.recomputed_usd == span.stored_cost_usd
    assert explanation.stored_cost_usd == span.stored_cost_usd
    assert _kinds(explanation) == TOKEN_KINDS
    for line in explanation.lines:
        assert line.unit == "tokens"
        assert line.rate_unit == "usd_per_mtok"
        assert line.multiplier == 1
        if line.quantity == 0:
            assert line.usd == 0
    assert explanation.message is None


def test_pilot_five_minute_write_lines_carry_the_classes_and_rates() -> None:
    explanation = _explain(_span())

    quantities = [(line.kind, line.quantity, line.rate) for line in explanation.lines]
    assert quantities == [
        ("uncached_input", 19, Decimal("1.00")),
        ("cache_read", 0, Decimal("0.10")),
        ("cache_write_5m", 6404, Decimal("1.25")),
        ("cache_write_1h", 0, Decimal("2.00")),
        ("output", 10, Decimal("5.00")),
    ]
    assert _line(explanation, "cache_write_5m").usd == Decimal("0.008005")


# --- tier multipliers ----------------------------------------------------------------------


def test_batch_multiplier_halves_every_token_line() -> None:
    standard = _explain(_span())
    batch = _explain(_span(service_tier="batch", stored_cost_usd=Decimal("0.00403700")))

    assert batch.status == "ok"
    assert batch.tier_multiplier == Decimal("0.5")
    assert batch.tier_note is None
    assert _kinds(batch) == TOKEN_KINDS
    for standard_line, batch_line in zip(standard.lines, batch.lines, strict=True):
        assert batch_line.multiplier == Decimal("0.5")
        assert batch_line.rate == standard_line.rate
        assert batch_line.usd == standard_line.usd / 2
    assert batch.recomputed_usd == Decimal("0.00403700")


def test_flex_halves_and_openai_priority_is_standard_with_unmodelled_note() -> None:
    # gpt-4o, 1000 input and 100 output: 2.50 + 1.00 per million → 0.0035 standard.
    base = _span(
        provider="openai",
        model="gpt-4o",
        input_tokens=1000,
        output_tokens=100,
        cache_write_tokens=None,
    )

    flex = _explain(
        replace(base, service_tier="flex", stored_cost_usd=Decimal("0.00175000")), GPT_4O
    )
    assert flex.status == "ok"
    assert flex.tier_multiplier == Decimal("0.5")
    assert flex.tier_note is None
    assert all(line.multiplier == Decimal("0.5") for line in flex.lines)

    priority = _explain(
        replace(base, service_tier="priority", stored_cost_usd=Decimal("0.00350000")), GPT_4O
    )
    assert priority.status == "ok"
    assert priority.tier_multiplier == 1
    assert priority.tier_note == "unmodelled"
    assert len(priority.lines) == 5
    assert all(line.multiplier == 1 for line in priority.lines)
    assert priority.message is None


def test_anthropic_priority_is_one_with_commitment_note() -> None:
    explanation = _explain(_span(service_tier="priority"))

    assert explanation.status == "ok"
    assert explanation.tier_multiplier == 1
    assert explanation.tier_note == "commitment_billed"
    assert all(line.multiplier == 1 for line in explanation.lines)
    assert explanation.recomputed_usd == Decimal("0.00807400")


# --- server-tool fees ----------------------------------------------------------------------


def test_fee_line_added_and_recompute_includes_it() -> None:
    # Sonnet 4.6, 1000 uncached input and 100 output: 0.003 + 0.0015 = 0.0045 in tokens.
    span = _span(
        model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=100,
        cache_write_tokens=None,
        web_search_requests=3,
        stored_cost_usd=Decimal("0.03450000"),
        stored_fee_usd=Decimal("0.03000000"),
    )

    explanation = _explain(span, SONNET_46, WEB_SEARCH_FEE)

    assert explanation.status == "ok"
    assert _kinds(explanation) == (*TOKEN_KINDS, "web_search")
    fee_line = _line(explanation, "web_search")
    assert fee_line.quantity == 3
    assert fee_line.unit == "requests"
    assert fee_line.rate == Decimal("10.00")
    assert fee_line.rate_unit == "usd_per_1k_requests"
    assert fee_line.multiplier == 1
    assert fee_line.usd == Decimal("0.03")
    assert explanation.recomputed_usd == Decimal("0.00450000") + Decimal("0.03")
    assert explanation.stored_fee_usd == Decimal("0.03000000")


def test_fee_multiplier_stays_one_on_a_batch_span() -> None:
    span = _span(
        model="claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=100,
        cache_write_tokens=None,
        web_search_requests=3,
        service_tier="batch",
        stored_cost_usd=Decimal("0.03225000"),
        stored_fee_usd=Decimal("0.03000000"),
    )

    explanation = _explain(span, SONNET_46, WEB_SEARCH_FEE)

    assert explanation.status == "ok"
    assert _line(explanation, "web_search").multiplier == 1
    assert _line(explanation, "output").multiplier == Decimal("0.5")


def test_fee_line_shown_when_token_cost_unknown() -> None:
    # Review Focus 1: a web search on an unpriced model keeps its fee, and nothing else.
    span = _span(
        model="claude-unknown",
        web_search_requests=3,
        stored_cost_usd=None,
        stored_fee_usd=Decimal("0.03000000"),
        pricing_version=None,
    )

    explanation = _explain(span, None, WEB_SEARCH_FEE, lookup="no_price")

    assert explanation.status == "no_price"
    assert _kinds(explanation) == ("web_search",)
    assert _line(explanation, "web_search").usd == Decimal("0.03")
    assert explanation.recomputed_usd is None
    assert explanation.stored_cost_usd is None
    assert explanation.stored_fee_usd == Decimal("0.03000000")
    assert explanation.message == "Token cost unknown; only the web search fee is priced."


def test_searches_without_a_fee_rate_are_named_in_the_message() -> None:
    span = _span(web_search_requests=2, provider="anthropic")

    explanation = _explain(span, HAIKU_45, None)

    assert explanation.status == "ok"
    assert _kinds(explanation) == TOKEN_KINDS
    assert explanation.message == "No fee rate for anthropic web search in snapshot 2026-10-15."


# --- reasoning tokens ----------------------------------------------------------------------


def test_reasoning_tokens_are_a_breakdown_not_a_line() -> None:
    # 19 uncached + 6404 written + 812 output: 19 + 8005 + 4060 micro-USD.
    span = _span(output_tokens=812, reasoning_tokens=300, stored_cost_usd=Decimal("0.01208400"))

    explanation = _explain(span)

    assert explanation.status == "ok"
    assert explanation.output_breakdown == (300, 512)
    assert _kinds(explanation) == TOKEN_KINDS
    assert _line(explanation, "output").quantity == 812


def test_output_breakdown_is_none_without_reasoning_tokens() -> None:
    assert _explain(_span()).output_breakdown is None


# --- non-ok statuses -----------------------------------------------------------------------


def test_cache_hit_lines_all_zero() -> None:
    span = _span(is_cache_hit=True, stored_cost_usd=Decimal("0.00000000"))

    explanation = _explain(span)

    assert explanation.status == "cache_hit"
    assert _kinds(explanation) == TOKEN_KINDS
    assert all(line.quantity == 0 and line.usd == 0 for line in explanation.lines)
    assert explanation.recomputed_usd == Decimal("0.00000000")
    assert explanation.stored_cost_usd == Decimal("0.00000000")
    assert explanation.message is not None


@pytest.mark.parametrize(
    ("reason", "sentence"),
    [
        ("client_disconnected", "the client disconnected"),
        ("stream_error", "the stream failed"),
        ("stream_cut_by_fault", "a Lab fault cut the stream"),
        ("include_usage_not_set", "stream_options.include_usage was not set"),
        ("no_final_usage", "the provider sent no final usage event"),
        ("not_exported", "the exporter sent no usage"),
    ],
)
def test_no_usage_echoes_reason(reason: str, sentence: str) -> None:
    span = _span(output_tokens=None, usage_unreported_reason=reason, stored_cost_usd=None)

    explanation = _explain(span)

    assert explanation.status == "no_usage"
    assert explanation.lines == ()
    assert explanation.recomputed_usd is None
    assert explanation.stored_cost_usd is None
    assert explanation.message == (
        f"Output tokens were not reported: {sentence}, so the cost is unknown."
    )


def test_unreported_reason_copy_covers_the_six_values() -> None:
    assert set(UNREPORTED_REASON_COPY) == {
        "client_disconnected",
        "stream_error",
        "stream_cut_by_fault",
        "include_usage_not_set",
        "no_final_usage",
        "not_exported",
    }


def test_no_usage_without_a_reason_still_says_unknown() -> None:
    explanation = _explain(_span(input_tokens=None, stored_cost_usd=None))

    assert explanation.status == "no_usage"
    assert explanation.lines == ()
    assert explanation.message is not None
    assert "unknown" in explanation.message


def test_no_usage_with_searches_keeps_fee_line() -> None:
    # A cut stream that still counted searches: the fee is priced, the token cost is not.
    span = _span(
        output_tokens=None,
        usage_unreported_reason="client_disconnected",
        web_search_requests=2,
        stored_cost_usd=None,
        stored_fee_usd=Decimal("0.02000000"),
    )

    explanation = _explain(span, HAIKU_45, WEB_SEARCH_FEE)

    assert explanation.status == "no_usage"
    assert _kinds(explanation) == ("web_search",)
    assert _line(explanation, "web_search").usd == Decimal("0.02")
    assert explanation.recomputed_usd is None
    assert explanation.stored_fee_usd == Decimal("0.02000000")
    assert explanation.message == (
        "Output tokens were not reported: the client disconnected, so the cost is unknown."
    )


def test_no_price() -> None:
    span = _span(model="claude-unknown", stored_cost_usd=None, pricing_version=None)

    explanation = _explain(span, None, lookup="no_price")

    assert explanation.status == "no_price"
    assert explanation.lines == ()
    assert explanation.recomputed_usd is None
    assert explanation.stored_cost_usd is None
    assert explanation.message is not None
    assert "anthropic" in explanation.message
    assert "claude-unknown" in explanation.message


def test_no_price_with_searches_but_no_fee_rate_leads_with_the_missing_price() -> None:
    # Google bills no web search fee in the book: the main fact is still the missing price.
    span = _span(
        provider="google",
        model="gemini-unknown",
        web_search_requests=2,
        stored_cost_usd=None,
        pricing_version=None,
    )

    explanation = _explain(span, None, None, lookup="no_price")

    assert explanation.status == "no_price"
    assert explanation.lines == ()
    assert explanation.message == (
        "No price matched google gemini-unknown when the span was ingested, "
        "so the cost is unknown. No fee rate for google web search."
    )


def test_price_version_missing() -> None:
    span = _span(pricing_version="2025-01-01")

    explanation = _explain(span, None, lookup="version_missing")

    assert explanation.status == "price_version_missing"
    assert explanation.lines == ()
    assert explanation.recomputed_usd is None
    assert explanation.stored_cost_usd == Decimal("0.00807400")
    assert explanation.message is not None
    assert "2025-01-01" in explanation.message


def test_override_deleted_shows_stored_figure() -> None:
    # Review Focus 5: the figure the deleted override produced is explained, never re-priced.
    span = _span(pricing_version="override:0199c0de-0000-7000-8000-000000000001")

    explanation = _explain(span, None, lookup="override_deleted")

    assert explanation.status == "override_deleted"
    assert explanation.lines == ()
    assert explanation.recomputed_usd is None
    assert explanation.stored_cost_usd == Decimal("0.00807400")
    assert explanation.message is not None
    assert "deleted" in explanation.message


def test_pre_0600_span_explains_as_standard_without_fee() -> None:
    # Review Focus 5: a span stored before 0600 has no tier, no search count and no fee.
    span = _span(service_tier=None, web_search_requests=None, stored_fee_usd=None)

    explanation = _explain(span, HAIKU_45, WEB_SEARCH_FEE)

    assert explanation.status == "ok"
    assert _kinds(explanation) == TOKEN_KINDS
    assert explanation.tier_multiplier == 1
    assert explanation.tier_note is None
    assert explanation.stored_fee_usd is None
    assert explanation.recomputed_usd == Decimal("0.00807400")
    assert explanation.message is None


def test_altered_stored_cost_is_mismatch() -> None:
    explanation = _explain(_span(stored_cost_usd=Decimal("0.00807500")))

    assert explanation.status == "mismatch"
    assert _kinds(explanation) == TOKEN_KINDS
    assert explanation.recomputed_usd == Decimal("0.00807400")
    assert explanation.stored_cost_usd == Decimal("0.00807500")
    assert explanation.message == (
        "Recomputed 0.00807400 differs from stored 0.00807500. "
        "This is a bug in Spanlight; report it with the span id."
    )


def test_usage_rollup_never_mismatches() -> None:
    span = _span(stored_cost_usd=Decimal("0.00807500"), usage_rollup=True)

    explanation = _explain(span)

    assert explanation.status == "usage_rollup"
    assert _kinds(explanation) == TOKEN_KINDS
    assert explanation.recomputed_usd == Decimal("0.00807400")
    assert explanation.stored_cost_usd == Decimal("0.00807500")
    assert explanation.message == (
        "Usage rollup: this span repeats the usage of its child spans and is excluded from totals."
    )


def test_usage_rollup_comes_before_cache_hit_and_no_usage() -> None:
    span = _span(usage_rollup=True, is_cache_hit=True, output_tokens=None, stored_cost_usd=None)

    explanation = _explain(span)

    assert explanation.status == "usage_rollup"
    assert explanation.lines == ()
    assert explanation.recomputed_usd is None


def test_cache_hit_comes_before_no_usage() -> None:
    span = _span(is_cache_hit=True, output_tokens=None, stored_cost_usd=Decimal("0.00000000"))

    assert _explain(span).status == "cache_hit"


def test_explanation_is_frozen() -> None:
    explanation = _explain(_span())

    with pytest.raises(AttributeError):
        explanation.status = "mismatch"  # type: ignore[misc]

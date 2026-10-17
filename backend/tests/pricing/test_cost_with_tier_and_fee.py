"""Service-tier multipliers, server-tool fees, provider aliases and version lookup in the price
book (pure, no database). The book holds every bundled snapshot, as `load_price_book` does."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.pricing.cost import CostResult, Price, PriceBook
from app.pricing.explain import SpanCostInputs, explain_span
from app.pricing.fees import FeeBook, ServerToolFee
from app.pricing.prices import ALL_SNAPSHOTS, PRICING_VERSION
from app.pricing.tiers import multipliers_for

AT = datetime(2026, 10, 16, 12, tzinfo=UTC)
SONNET_46 = "claude-sonnet-4-6"


def _all_prices() -> list[Price]:
    return [
        Price(
            provider=seed.provider,
            model_pattern=seed.model_pattern,
            input_per_mtok=seed.input_per_mtok,
            output_per_mtok=seed.output_per_mtok,
            cached_input_per_mtok=seed.cached_input_per_mtok,
            effective_from=snapshot.effective_from,
            version=snapshot.version,
            cache_write_5m_per_mtok=seed.cache_write_5m_per_mtok,
            cache_write_1h_per_mtok=seed.cache_write_1h_per_mtok,
            min_prompt_tokens=seed.min_prompt_tokens,
        )
        for snapshot in ALL_SNAPSHOTS
        for seed in snapshot.prices
    ]


def _all_fees() -> FeeBook:
    return FeeBook(
        ServerToolFee(
            provider=fee.provider,
            tool=fee.tool,
            usd_per_1k_requests=fee.usd_per_1k_requests,
            effective_from=snapshot.effective_from,
            version=snapshot.version,
        )
        for snapshot in ALL_SNAPSHOTS
        for fee in snapshot.fees
    )


@pytest.fixture
def book() -> PriceBook:
    return PriceBook(_all_prices(), fees=_all_fees())


def _cost(
    book: PriceBook,
    provider: str | None,
    model: str | None,
    *,
    input_tokens: int | None = 1000,
    output_tokens: int | None = 100,
    cached_tokens: int | None = None,
    service_tier: str | None = None,
    web_search_requests: int | None = None,
) -> CostResult | None:
    return book.cost(
        provider=provider,
        model=model,
        at=AT,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        service_tier=service_tier,
        web_search_requests=web_search_requests,
    )


def test_batch_tier_halves_token_cost(book: PriceBook) -> None:
    # (1000 * 3.00 + 100 * 15.00) / 1e6 = 0.0045, halved.
    result = _cost(book, "anthropic", SONNET_46, service_tier="batch")
    assert result == CostResult(
        cost_usd=Decimal("0.00225000"), fee_usd=None, pricing_version=PRICING_VERSION
    )


@pytest.mark.parametrize(
    ("at", "version"),
    [
        (datetime(2026, 10, 15, 12, tzinfo=UTC), "2026-10-15"),
        (datetime(2026, 10, 5, 12, tzinfo=UTC), "2026-10-01"),
    ],
)
def test_batch_span_dated_before_current_snapshot_is_halved(
    book: PriceBook, at: datetime, version: str
) -> None:
    # The released snapshots carry no tier table, but batch was half price on those dates too:
    # the span is priced by its own snapshot's rates at the batch factor, and the explanation
    # reproduces the stored figure with that factor.
    usage = {"input_tokens": 1000, "output_tokens": 100, "cached_tokens": None}
    batch = book.cost(provider="anthropic", model=SONNET_46, at=at, service_tier="batch", **usage)
    standard = book.cost(provider="anthropic", model=SONNET_46, at=at, **usage)
    assert standard is not None and standard.cost_usd == Decimal("0.00450000")
    assert batch == CostResult(
        cost_usd=Decimal("0.00225000"), fee_usd=None, pricing_version=version
    )
    flex = book.cost(provider="openai", model="gpt-4o", at=at, service_tier="flex", **usage)
    assert flex is not None and flex.cost_usd == Decimal("0.00175000")

    span = SpanCostInputs(
        provider="anthropic",
        model=SONNET_46,
        started_at=at,
        cache_write_tokens=None,
        cache_write_1h_tokens=None,
        web_search_requests=None,
        service_tier="batch",
        reasoning_tokens=None,
        usage_unreported_reason=None,
        stored_cost_usd=batch.cost_usd,
        stored_fee_usd=None,
        pricing_version=version,
        is_cache_hit=False,
        **usage,
    )
    price = book.find_by_version(version, "anthropic", SONNET_46, at, prompt_tokens=1000)
    explanation = explain_span(span, price, None, multipliers_for(version, at), "found")
    assert explanation.status == "ok"
    assert explanation.recomputed_usd == Decimal("0.00225000")
    assert (explanation.tier_multiplier, explanation.tier_note) == (Decimal("0.5"), None)


def test_openai_priority_prices_at_standard_rate(book: PriceBook) -> None:
    # No OpenAI priority multiplier: Fast is billed per model (ruling-openai-priority).
    # (1000 * 2.50 + 100 * 10.00) / 1e6.
    priority = _cost(book, "openai", "gpt-4o", service_tier="priority")
    standard = _cost(book, "openai", "gpt-4o", service_tier=None)
    assert priority is not None and priority.cost_usd == Decimal("0.00350000")
    assert priority == standard


def test_flex_halves(book: PriceBook) -> None:
    result = _cost(book, "openai", "gpt-4o", service_tier="flex")
    assert result is not None and result.cost_usd == Decimal("0.00175000")


@pytest.mark.parametrize("tier", ["standard", "turbo", None])
def test_unknown_tier_prices_standard(book: PriceBook, tier: str | None) -> None:
    result = _cost(book, "anthropic", SONNET_46, service_tier=tier)
    assert result is not None and result.cost_usd == Decimal("0.00450000")


def test_anthropic_priority_is_commitment_billed_at_standard_rate(book: PriceBook) -> None:
    result = _cost(book, "anthropic", SONNET_46, service_tier="priority")
    assert result is not None and result.cost_usd == Decimal("0.00450000")


def test_web_search_fee_added_to_cost(book: PriceBook) -> None:
    result = _cost(book, "anthropic", SONNET_46, web_search_requests=3)
    assert result is not None
    assert result.fee_usd == Decimal("0.03000000")
    assert result.cost_usd == Decimal("0.00450000") + Decimal("0.03")
    assert result.cost_usd == Decimal("0.03450000")
    assert result.pricing_version == PRICING_VERSION


def test_fee_is_not_multiplied_by_the_tier(book: PriceBook) -> None:
    result = _cost(book, "anthropic", SONNET_46, service_tier="batch", web_search_requests=3)
    assert result is not None
    assert result.fee_usd == Decimal("0.03000000")
    assert result.cost_usd == Decimal("0.03225000")


def test_token_cost_and_fee_are_rounded_once() -> None:
    # 1 token at 0.000005/token = 0.000000005 rounds to 1e-8 alone; one search at 0.000000005 too.
    # Rounded separately and added: 0.00000002. Summed raw, then rounded once: 0.00000001.
    price = Price("anthropic", "acme-1", Decimal("0.005"), Decimal(0), None, AT, "v")
    fee = ServerToolFee("anthropic", "web_search", Decimal("0.000005"), AT, "v")
    book = PriceBook([price], fees=FeeBook([fee]))
    result = _cost(book, "anthropic", "acme-1", input_tokens=1, output_tokens=0)
    with_fee = _cost(
        book, "anthropic", "acme-1", input_tokens=1, output_tokens=0, web_search_requests=1
    )
    assert result is not None and result.cost_usd == Decimal("0.00000001")
    assert with_fee is not None
    assert with_fee.fee_usd == Decimal("0.00000001")
    assert with_fee.cost_usd == Decimal("0.00000001")


def test_fee_without_token_cost_keeps_cost_null(book: PriceBook) -> None:
    result = _cost(book, "anthropic", "claude-unknown-9", web_search_requests=3)
    assert result == CostResult(cost_usd=None, fee_usd=Decimal("0.03000000"), pricing_version=None)


def test_fee_without_output_tokens_keeps_cost_null(book: PriceBook) -> None:
    # A cut stream that still counted its searches: the fee is known, the token cost is not.
    result = _cost(book, "anthropic", SONNET_46, output_tokens=None, web_search_requests=3)
    assert result == CostResult(cost_usd=None, fee_usd=Decimal("0.03000000"), pricing_version=None)


def test_nothing_priced_is_none(book: PriceBook) -> None:
    assert _cost(book, "anthropic", "claude-unknown-9") is None
    assert _cost(book, "anthropic", "claude-unknown-9", web_search_requests=0) == CostResult(
        cost_usd=None, fee_usd=Decimal(0), pricing_version=None
    )
    assert _cost(book, "google", "gemini-unknown", web_search_requests=3) is None


def test_span_without_provider_gets_no_fee(book: PriceBook) -> None:
    result = _cost(book, None, SONNET_46, web_search_requests=3)
    assert result is not None
    assert result.fee_usd is None
    assert result.cost_usd == Decimal("0.00450000")


def test_google_search_without_fee_row_is_none(book: PriceBook) -> None:
    result = _cost(book, "google", "gemini-2.5-flash", web_search_requests=3)
    assert result is not None
    assert result.fee_usd is None
    assert result.cost_usd == Decimal("0.00055000")


def test_zero_searches_is_a_known_zero_fee(book: PriceBook) -> None:
    result = _cost(book, "anthropic", SONNET_46, web_search_requests=0)
    assert result is not None
    assert result.fee_usd == Decimal(0)
    assert result.cost_usd == Decimal("0.00450000")


def test_gemini_alias_prices_as_google(book: PriceBook) -> None:
    # (1000 * 0.30 + 100 * 2.50) / 1e6.
    result = _cost(book, "gemini", "gemini-2.5-flash")
    assert result == CostResult(
        cost_usd=Decimal("0.00055000"), fee_usd=None, pricing_version="2026-10-16"
    )
    assert _cost(book, " Vertex_AI ", "gemini-2.5-flash") == result


def test_claude_alias_finds_the_anthropic_fee(book: PriceBook) -> None:
    result = _cost(book, "claude", SONNET_46, web_search_requests=3)
    assert result is not None and result.fee_usd == Decimal("0.03000000")


def test_gemini_tier_at_200001(book: PriceBook) -> None:
    base = _cost(book, "google", "gemini-2.5-pro", input_tokens=200_000, output_tokens=0)
    upper = _cost(book, "google", "gemini-2.5-pro", input_tokens=200_001, output_tokens=0)
    # 200,000 * 1.25 / 1e6; 200,001 * 2.50 / 1e6.
    assert base is not None and base.cost_usd == Decimal("0.25000000")
    assert upper is not None and upper.cost_usd == Decimal("0.50000250")


def test_find_by_version_ignores_other_snapshots(book: PriceBook) -> None:
    # Sonnet 5.5's cache read was 0.20 in 2026-10-15 and 0.10 from 2026-10-16.
    current = book.find("anthropic", "claude-sonnet-5-5", AT)
    older = book.find_by_version("2026-10-15", "anthropic", "claude-sonnet-5-5", AT)
    assert current is not None and current.version == "2026-10-16"
    assert current.cached_input_per_mtok == Decimal("0.10")
    assert older is not None and older.version == "2026-10-15"
    assert older.cached_input_per_mtok == Decimal("0.20")
    assert book.find_by_version("2026-10-15", "google", "gemini-2.5-flash", AT) is None
    assert book.find_by_version("2001-01-01", "anthropic", "claude-sonnet-5-5", AT) is None


def test_find_by_version_keeps_tier_precedence(book: PriceBook) -> None:
    base = book.find_by_version("2026-10-16", "gemini", "gemini-2.5-pro", AT, prompt_tokens=200_000)
    upper = book.find_by_version(
        "2026-10-16", "gemini", "gemini-2.5-pro", AT, prompt_tokens=200_001
    )
    assert base is not None and base.min_prompt_tokens == 0
    assert upper is not None and upper.min_prompt_tokens == 200_001


def test_has_version(book: PriceBook) -> None:
    assert book.has_version("2026-10-16")
    assert book.has_version("2026-10-01")
    assert not book.has_version("override:0190")
    assert not book.has_version("2001-01-01")


def test_openai_cost_unchanged_without_tier(book: PriceBook) -> None:
    # The figure `test_pricing.py::test_cached_tokens_priced_at_cached_rate` asserts.
    result = _cost(
        book, "openai", "gpt-4o", input_tokens=1_000_000, output_tokens=0, cached_tokens=400_000
    )
    assert result == CostResult(
        cost_usd=Decimal("2.00000000"), fee_usd=None, pricing_version=PRICING_VERSION
    )


def test_explanation_recomputes_the_price_book_figure(book: PriceBook) -> None:
    # Batch, all four prompt classes and a fee: the explanation and the price book share one
    # class split and one rounding, so the recomputed total is the stored cost to the 1e-8.
    usage = {
        "input_tokens": 10_000,
        "output_tokens": 700,
        "cached_tokens": 2_000,
        "cache_write_tokens": 5_000,
        "cache_write_1h_tokens": 1_500,
    }
    priced = book.cost(
        provider="anthropic",
        model=SONNET_46,
        at=AT,
        service_tier="batch",
        web_search_requests=3,
        **usage,
    )
    # (3000 * 3.00 + 2000 * 0.30 + 3500 * 3.75 + 1500 * 6.00 + 700 * 15.00) / 1e6 * 0.5 + 0.03.
    assert priced is not None and priced.cost_usd == Decimal("0.05111250")
    assert priced.pricing_version is not None
    span = SpanCostInputs(
        provider="anthropic",
        model=SONNET_46,
        started_at=AT,
        web_search_requests=3,
        service_tier="batch",
        reasoning_tokens=None,
        usage_unreported_reason=None,
        stored_cost_usd=priced.cost_usd,
        stored_fee_usd=priced.fee_usd,
        pricing_version=priced.pricing_version,
        is_cache_hit=False,
        **usage,
    )
    price = book.find_by_version(
        priced.pricing_version, "anthropic", SONNET_46, AT, prompt_tokens=10_000
    )
    fee = _all_fees().find("anthropic", "web_search", AT)
    explanation = explain_span(
        span, price, fee, multipliers_for(priced.pricing_version, AT), "found"
    )
    assert explanation.status == "ok"
    assert explanation.recomputed_usd == priced.cost_usd

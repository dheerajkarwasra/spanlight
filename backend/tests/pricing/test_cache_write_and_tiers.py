"""Cache-write premiums per TTL and prompt-length price tiers (pure, no database).

The expected costs of the three pilot cases are Anthropic's own billed amounts for real calls
made on 2026-10-14 (`docs/plans/reports/pilot-2026-10-14.md`, D1 and D2), exact to 1e-8.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.pricing.cost import OVERRIDE_VERSION_PREFIX, Price, PriceBook
from app.pricing.prices import EFFECTIVE_FROM, PRICING_VERSION, SEED_PRICES

AT = datetime(2026, 10, 15, 12, tzinfo=UTC)

HAIKU_45 = "claude-haiku-4-5"
HAIKU_55 = "claude-haiku-5-5"


def _seed_prices() -> list[Price]:
    return [
        Price(
            provider=seed.provider,
            model_pattern=seed.model_pattern,
            input_per_mtok=seed.input_per_mtok,
            output_per_mtok=seed.output_per_mtok,
            cached_input_per_mtok=seed.cached_input_per_mtok,
            effective_from=EFFECTIVE_FROM,
            version=PRICING_VERSION,
            cache_write_5m_per_mtok=seed.cache_write_5m_per_mtok,
            cache_write_1h_per_mtok=seed.cache_write_1h_per_mtok,
            min_prompt_tokens=seed.min_prompt_tokens,
        )
        for seed in SEED_PRICES
    ]


@pytest.fixture
def book() -> PriceBook:
    return PriceBook(_seed_prices())


def _cost(
    book: PriceBook,
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    cache_write_1h_tokens: int | None = None,
    provider: str | None = "anthropic",
) -> Decimal:
    result = book.cost(
        provider=provider,
        model=model,
        at=AT,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        cache_write_tokens=cache_write_tokens,
        cache_write_1h_tokens=cache_write_1h_tokens,
    )
    assert result is not None
    assert result.pricing_version == PRICING_VERSION
    return result.cost_usd


def test_haiku45_5m_write(book: PriceBook) -> None:
    # 19 uncached * 1.00 + 6404 written (5 m) * 1.25 + 10 output * 5.00
    assert _cost(
        book,
        HAIKU_45,
        input_tokens=6423,
        output_tokens=10,
        cached_tokens=0,
        cache_write_tokens=6404,
    ) == Decimal("0.00807400")


def test_haiku45_1h_write(book: PriceBook) -> None:
    # 19 uncached * 1.00 + 6404 written (1 h) * 2.00 + 12 output * 5.00
    assert _cost(
        book,
        HAIKU_45,
        input_tokens=6423,
        output_tokens=12,
        cache_write_tokens=6404,
        cache_write_1h_tokens=6404,
    ) == Decimal("0.01288700")


def test_haiku55_over_100k_tier(book: PriceBook) -> None:
    # The whole prompt bills at the upper tier: 157718 * 0.50 + 64 * 2.50
    assert _cost(book, HAIKU_55, input_tokens=157_718, output_tokens=64) == Decimal("0.07901900")


def test_tier_boundary(book: PriceBook) -> None:
    base = book.find("anthropic", HAIKU_55, AT, prompt_tokens=100_000)
    upper = book.find("anthropic", HAIKU_55, AT, prompt_tokens=100_001)
    assert base is not None and base.min_prompt_tokens == 0
    assert upper is not None and upper.min_prompt_tokens == 100_001
    assert _cost(book, HAIKU_55, input_tokens=100_000, output_tokens=0) == Decimal("0.01000000")
    assert _cost(book, HAIKU_55, input_tokens=100_001, output_tokens=0) == Decimal("0.05000050")


def test_write_without_rate_falls_back_to_input() -> None:
    book = PriceBook([Price("acme", "acme-1", Decimal(2), Decimal(4), None, EFFECTIVE_FROM, "v")])
    result = book.cost(
        provider="acme",
        model="acme-1",
        at=AT,
        input_tokens=1_000_000,
        output_tokens=0,
        cached_tokens=None,
        cache_write_tokens=500_000,
        cache_write_1h_tokens=250_000,
    )
    assert result is not None and result.cost_usd == Decimal("2.00000000")


def test_writes_clamped_to_prompt(book: PriceBook) -> None:
    # 6000 reads leave 423 prompt tokens, so only 423 can have been written: no negative uncached.
    # 6000 * 0.10 + 423 * 1.25 + 0 uncached
    assert _cost(
        book,
        HAIKU_45,
        input_tokens=6423,
        output_tokens=0,
        cached_tokens=6000,
        cache_write_tokens=6000,
    ) == Decimal("0.00112875")


def test_override_of_base_tier_keeps_seed_upper_tier() -> None:
    override = Price(
        provider="anthropic",
        model_pattern=HAIKU_55,
        input_per_mtok=Decimal("0.05"),
        output_per_mtok=Decimal("0.25"),
        cached_input_per_mtok=Decimal("0.005"),
        effective_from=EFFECTIVE_FROM,
        version=f"{OVERRIDE_VERSION_PREFIX}x",
        source="override",
    )
    book = PriceBook([*_seed_prices(), override])

    small = book.cost(
        provider="anthropic",
        model=HAIKU_55,
        at=AT,
        input_tokens=50_000,
        output_tokens=0,
        cached_tokens=None,
    )
    assert small is not None
    assert small.pricing_version == override.version
    assert small.cost_usd == Decimal("0.00250000")

    large = book.cost(
        provider="anthropic",
        model=HAIKU_55,
        at=AT,
        input_tokens=157_718,
        output_tokens=64,
        cached_tokens=None,
    )
    assert large is not None
    assert large.pricing_version == PRICING_VERSION
    assert large.cost_usd == Decimal("0.07901900")


def test_openai_cost_unchanged(book: PriceBook) -> None:
    # The value `tests/pricing/test_pricing.py::test_cached_tokens_priced_at_cached_rate` expects.
    assert _cost(
        book,
        "gpt-4o",
        provider="openai",
        input_tokens=1_000_000,
        output_tokens=0,
        cached_tokens=400_000,
    ) == Decimal("2.00000000")

"""Cost calculation rules (pure, no database)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.core.model_match import matches_model
from app.pricing.cost import Price, PriceBook
from app.pricing.prices import EFFECTIVE_FROM, PRICING_VERSION, SEED_PRICES

AT = datetime(2026, 10, 16, 12, tzinfo=UTC)


@pytest.fixture
def book() -> PriceBook:
    return PriceBook(
        Price(
            provider=seed.provider,
            model_pattern=seed.model_pattern,
            input_per_mtok=seed.input_per_mtok,
            output_per_mtok=seed.output_per_mtok,
            cached_input_per_mtok=seed.cached_input_per_mtok,
            effective_from=EFFECTIVE_FROM,
            version=PRICING_VERSION,
        )
        for seed in SEED_PRICES
    )


def _cost(
    book: PriceBook,
    model: str | None,
    *,
    provider: str | None = None,
    input_tokens: int | None = 1_000_000,
    output_tokens: int | None = 1_000_000,
    cached_tokens: int | None = None,
) -> Decimal | None:
    result = book.cost(
        provider=provider,
        model=model,
        at=AT,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
    )
    return result.cost_usd if result else None


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("gpt-4o", "12.50000000"),
        ("gpt-4o-2024-08-06", "12.50000000"),
        ("gpt-4o-mini", "0.75000000"),
        ("gpt-4o-mini-2024-07-18", "0.75000000"),
        ("gpt-4.1-mini", "2.00000000"),
        ("o4-mini", "5.50000000"),
        ("claude-haiku-4-5", "6.00000000"),
        ("claude-haiku-4-5-20251001", "6.00000000"),
        ("claude-sonnet-4-5", "18.00000000"),
    ],
)
def test_known_models_and_snapshots(book: PriceBook, model: str, expected: str) -> None:
    assert _cost(book, model) == Decimal(expected)


@pytest.mark.parametrize(
    "model", ["gpt-4.1-nano", "gpt-4o-audio-preview", "claude-3-opus", "llama-3-70b", ""]
)
def test_unknown_models_cost_null_never_zero(book: PriceBook, model: str) -> None:
    assert _cost(book, model) is None


@pytest.mark.parametrize(
    "model", ["GPT-4o", "Claude-Sonnet-4-5", "gpt-4o-LATEST", " gpt-4o", "gpt-4o "]
)
def test_model_match_is_exact_about_case_and_whitespace(book: PriceBook, model: str) -> None:
    # Ingestion strips surrounding whitespace before pricing; the price lookup itself compares
    # the name as given, as the alert, rollup and budget filters compare the stored name.
    assert _cost(book, model) is None


@pytest.mark.parametrize("model", ["gpt-4o", "GPT-4o", "gpt-4o-2024-08-06", "Gpt-4o-latest"])
def test_pricing_and_model_filters_follow_one_rule(book: PriceBook, model: str) -> None:
    assert (book.find("openai", model, AT) is not None) == matches_model("gpt-4o", model)


def test_missing_usage_costs_null(book: PriceBook) -> None:
    assert _cost(book, "gpt-4o", input_tokens=None) is None
    assert _cost(book, "gpt-4o", output_tokens=None) is None
    assert _cost(book, None) is None


def test_zero_usage_costs_zero(book: PriceBook) -> None:
    assert _cost(book, "gpt-4o", input_tokens=0, output_tokens=0) == Decimal(0)


def test_cached_tokens_priced_at_cached_rate(book: PriceBook) -> None:
    # 600k * 2.50 + 400k * 1.25 + 0 output
    assert _cost(
        book, "gpt-4o", input_tokens=1_000_000, output_tokens=0, cached_tokens=400_000
    ) == Decimal("2.00000000")


def test_cached_tokens_fall_back_to_input_rate() -> None:
    book = PriceBook(
        [
            Price("acme", "acme-1", Decimal(2), Decimal(4), None, EFFECTIVE_FROM, "v"),
        ]
    )
    result = book.cost(
        provider="acme",
        model="acme-1",
        at=AT,
        input_tokens=1_000_000,
        output_tokens=0,
        cached_tokens=500_000,
    )
    assert result is not None and result.cost_usd == Decimal("2.00000000")


def test_provider_must_match_when_given(book: PriceBook) -> None:
    assert _cost(book, "gpt-4o", provider="anthropic") is None
    assert _cost(book, "gpt-4o", provider="OpenAI") == Decimal("12.50000000")


def test_price_effective_at_span_start() -> None:
    old = Price("p", "m", Decimal(1), Decimal(1), None, datetime(2025, 1, 1, tzinfo=UTC), "old")
    new = Price("p", "m", Decimal(2), Decimal(2), None, datetime(2026, 6, 1, tzinfo=UTC), "new")
    book = PriceBook([old, new])
    before = book.cost(
        provider="p",
        model="m",
        at=datetime(2026, 1, 1, tzinfo=UTC),
        input_tokens=1_000_000,
        output_tokens=0,
        cached_tokens=None,
    )
    after = book.cost(
        provider="p", model="m", at=AT, input_tokens=1_000_000, output_tokens=0, cached_tokens=None
    )
    too_early = book.cost(
        provider="p",
        model="m",
        at=datetime(2024, 1, 1, tzinfo=UTC),
        input_tokens=1,
        output_tokens=1,
        cached_tokens=None,
    )
    assert before is not None and before.pricing_version == "old"
    assert after is not None and after.pricing_version == "new"
    assert too_early is None

"""The provider alias table of the cost spec (§7.8). Pure: no database.

Failure modes: a Gemini span sent through a Vertex or GenAI integration carries a provider name
the price table never seeded, so its price lookup returns NULL and the span shows as unpriced; an
organization's override saved under an alias never matches a span; and an Azure span is priced at
OpenAI's public rates although Azure bills its own.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.pricing.cost import Price, PriceBook
from app.pricing.fees import FeeBook, ServerToolFee
from app.pricing.provider_alias import (
    ANTHROPIC_ALIASES,
    GOOGLE_ALIASES,
    OPENAI_ALIASES,
    canonical_provider,
)

GOOGLE_NAMES = [
    "gemini",
    "google",
    "google_genai",
    "google-genai",
    "google_ai",
    "vertex_ai",
    "vertexai",
    "gcp.gemini",
    "gcp.vertex_ai",
    "gcp.gen_ai",
]
ANTHROPIC_NAMES = ["anthropic", "claude"]
# Azure is deliberately not an OpenAI alias: Azure bills its own rates and no Azure price is
# seeded, so an Azure span must stay unpriced (NULL) rather than borrow OpenAI's public rates.
OPENAI_NAMES = ["openai"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        *[(name, "google") for name in GOOGLE_NAMES],
        *[(name, "anthropic") for name in ANTHROPIC_NAMES],
        *[(name, "openai") for name in OPENAI_NAMES],
        (" Gemini ", "google"),
        ("azure", "azure"),
        ("Azure_OpenAI", "azure_openai"),
        ("OpenRouter", "openrouter"),
        (None, None),
        ("", None),
    ],
)
def test_alias_table(name: str | None, expected: str | None) -> None:
    assert canonical_provider(name) == expected


def test_alias_sets_match_the_spec() -> None:
    assert frozenset(GOOGLE_NAMES) == GOOGLE_ALIASES
    assert frozenset(ANTHROPIC_NAMES) == ANTHROPIC_ALIASES
    assert frozenset(OPENAI_NAMES) == OPENAI_ALIASES


def test_whitespace_only_is_unknown() -> None:
    assert canonical_provider("   ") is None


AT = datetime(2026, 10, 16, 12, tzinfo=UTC)
EFFECTIVE = datetime(2026, 1, 1, tzinfo=UTC)


def _price(provider: str, version: str, *, override: bool = False) -> Price:
    """A `gemini-2.5-flash` row at 1.00 / 2.00 per million tokens."""
    return Price(
        provider=provider,
        model_pattern="gemini-2.5-flash",
        input_per_mtok=Decimal("1.00"),
        output_per_mtok=Decimal("2.00"),
        cached_input_per_mtok=None,
        effective_from=EFFECTIVE,
        version=version,
        source="override" if override else "seed",
    )


@pytest.mark.parametrize("override_provider", ["gemini", "vertex_ai", "google"])
@pytest.mark.parametrize("span_provider", ["gemini", "vertex_ai", "google", " GCP.Gen_AI "])
def test_override_saved_under_an_alias_matches_every_alias(
    override_provider: str, span_provider: str
) -> None:
    override = _price(override_provider, "override:1", override=True)
    book = PriceBook([_price("google", "2026-10-16"), override])

    assert book.find(span_provider, "gemini-2.5-flash", AT) == override
    assert book.find_by_version("override:1", span_provider, "gemini-2.5-flash", AT) == override


def test_alias_override_does_not_match_another_provider() -> None:
    book = PriceBook([_price("gemini", "override:1", override=True)])

    assert book.find("openai", "gemini-2.5-flash", AT) is None


def test_azure_span_finds_no_openai_price() -> None:
    openai_row = Price(
        provider="openai",
        model_pattern="gpt-4o",
        input_per_mtok=Decimal("2.50"),
        output_per_mtok=Decimal("10.00"),
        cached_input_per_mtok=None,
        effective_from=EFFECTIVE,
        version="2026-10-16",
    )
    book = PriceBook([openai_row])

    for provider in ("azure", "azure_openai"):
        result = book.cost(
            provider=provider,
            model="gpt-4o",
            at=AT,
            input_tokens=1000,
            output_tokens=1000,
            cached_tokens=None,
        )
        assert result is None


def test_fee_row_saved_under_an_alias_matches_the_canonical_name() -> None:
    fee = ServerToolFee(
        provider="claude",
        tool="web_search",
        usd_per_1k_requests=Decimal("10.00"),
        effective_from=EFFECTIVE,
        version="2026-10-16",
    )
    fees = FeeBook([fee])

    assert fees.find("anthropic", "web_search", AT) == fee
    assert fees.find("claude", "web_search", AT) == fee


def test_two_spellings_of_one_provider_tie_break_on_version() -> None:
    # Overrides saved as `gemini` and `google` for the same pattern, tier and start both match a
    # span now. The winner must not depend on the order the rows were loaded in, or a replay could
    # price the same span differently.
    first = _price("gemini", "override:0b6f", override=True)
    second = _price("google", "override:f2a1", override=True)

    for rows in ([first, second], [second, first]):
        book = PriceBook(rows)
        for span_provider in ("gemini", "google", "vertex_ai"):
            assert book.find(span_provider, "gemini-2.5-flash", AT) == second

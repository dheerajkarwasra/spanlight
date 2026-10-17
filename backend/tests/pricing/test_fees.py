"""The server-tool fee book of the cost spec (§7.7). Pure: no database.

Failure mode: a web search billed per request is priced by the wrong row (a rate that is not yet
in force, or one that an older provider spelling never finds), or a missing count or rate is
turned into a zero fee instead of an unknown one.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.pricing.cost import _COST_QUANTUM
from app.pricing.fees import FEE_QUANTUM, FeeBook, ServerToolFee, fee_cost

OLD_RATE = ServerToolFee(
    provider="anthropic",
    tool="web_search",
    usd_per_1k_requests=Decimal("8.00"),
    effective_from=datetime(2026, 1, 1, tzinfo=UTC),
    version="2026-01-01",
)
CURRENT_RATE = ServerToolFee(
    provider="anthropic",
    tool="web_search",
    usd_per_1k_requests=Decimal("10.00"),
    effective_from=datetime(2026, 10, 16, tzinfo=UTC),
    version="2026-10-16",
)
FUTURE_RATE = ServerToolFee(
    provider="anthropic",
    tool="web_search",
    usd_per_1k_requests=Decimal("12.00"),
    effective_from=datetime(2027, 1, 1, tzinfo=UTC),
    version="2027-01-01",
)
OPENAI_RATE = ServerToolFee(
    provider="openai",
    tool="web_search",
    usd_per_1k_requests=Decimal("10.00"),
    effective_from=datetime(2026, 10, 16, tzinfo=UTC),
    version="2026-10-16",
)
AT = datetime(2026, 11, 1, tzinfo=UTC)


@pytest.fixture
def book() -> FeeBook:
    # Out of order on purpose: `find` must sort, not trust the input order.
    return FeeBook([FUTURE_RATE, OLD_RATE, OPENAI_RATE, CURRENT_RATE])


def test_find_picks_latest_effective_row_not_after_at(book: FeeBook) -> None:
    assert book.find("anthropic", "web_search", AT) == CURRENT_RATE
    assert book.find("anthropic", "web_search", datetime(2026, 6, 1, tzinfo=UTC)) == OLD_RATE
    assert book.find("anthropic", "web_search", CURRENT_RATE.effective_from) == CURRENT_RATE
    assert book.find("anthropic", "web_search", datetime(2025, 12, 31, tzinfo=UTC)) is None


def test_find_canonicalises_provider(book: FeeBook) -> None:
    assert book.find("claude", "web_search", AT) == CURRENT_RATE
    assert book.find(" Anthropic ", "web_search", AT) == CURRENT_RATE
    # Azure is not an OpenAI alias: it bills its own rates and none are seeded.
    assert book.find("azure_openai", "web_search", AT) is None


def test_find_unknown_provider_tool_or_missing_provider_is_none(book: FeeBook) -> None:
    assert book.find("openrouter", "web_search", AT) is None
    assert book.find("anthropic", "code_execution", AT) is None
    assert book.find(None, "web_search", AT) is None


def test_fee_cost_rounds() -> None:
    assert fee_cost(3, CURRENT_RATE) == Decimal("0.03000000")
    assert fee_cost(1, CURRENT_RATE) == Decimal("0.01000000")

    tiny = ServerToolFee(
        provider="anthropic",
        tool="web_search",
        usd_per_1k_requests=Decimal("0.0000001"),
        effective_from=CURRENT_RATE.effective_from,
        version="tiny",
    )
    rounded = fee_cost(1, tiny)
    assert rounded == Decimal("0E-8")
    assert rounded == Decimal(0)
    assert rounded is not None
    assert rounded.as_tuple().exponent == -8

    # Half-up at the eighth decimal, not banker's rounding.
    half = ServerToolFee(
        provider="anthropic",
        tool="web_search",
        usd_per_1k_requests=Decimal("0.000005"),
        effective_from=CURRENT_RATE.effective_from,
        version="half",
    )
    assert fee_cost(1, half) == Decimal("0.00000001")


def test_fee_cost_zero_requests_is_zero_not_none() -> None:
    assert fee_cost(0, CURRENT_RATE) == Decimal("0E-8")


def test_fee_cost_missing_rate_or_count_is_none() -> None:
    assert fee_cost(None, CURRENT_RATE) is None
    assert fee_cost(3, None) is None
    assert fee_cost(None, None) is None


def test_fee_quantum_matches_the_cost_quantum() -> None:
    assert FEE_QUANTUM == _COST_QUANTUM

"""Every bundled snapshot is synced, so a span is priced by the one in force when it started.

Ingestion accepts spans up to 90 days old and OTLP batches arrive late, so a fresh install must
price a span dated before the current snapshot exactly as an upgraded install does: by the
historical snapshot, under its version. A released snapshot is frozen: the `2026-10-15` rows are
pinned here as literals, so an edit to them fails loudly instead of silently re-pricing history.
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ServerToolFee as ServerToolFeeRow
from app.pricing.cost import load_price_book, sync_seed_prices
from app.pricing.prices import (
    ALL_SNAPSHOTS,
    CURRENT_SNAPSHOT,
    EFFECTIVE_FROM,
    HISTORICAL_SNAPSHOTS,
    PRICING_VERSION,
    SNAPSHOT_2026_10_01,
    SNAPSHOT_2026_10_15,
    SeedFee,
    SeedPrice,
)

# The pilot's 5-minute cache-write call on Claude Haiku 4.5 (D1).
USAGE = {"input_tokens": 6423, "output_tokens": 10, "cached_tokens": 0, "cache_write_tokens": 6404}

# The 19 rows of the released 2026-10-15 snapshot, verbatim:
# (provider, pattern, input, output, cached, write 5 m, write 1 h, min prompt tokens).
ROWS_2026_10_15: list[tuple[str, str, str, str, str, str | None, str | None, int]] = [
    ("anthropic", "claude-haiku-4-5", "1.00", "5.00", "0.10", "1.25", "2.00", 0),
    ("anthropic", "claude-sonnet-4-5", "3.00", "15.00", "0.30", "3.75", "6.00", 0),
    ("anthropic", "claude-sonnet-4-6", "3.00", "15.00", "0.30", "3.75", "6.00", 0),
    ("anthropic", "claude-opus-4-5", "5.00", "25.00", "0.50", "6.25", "10.00", 0),
    ("anthropic", "claude-opus-4-6", "5.00", "25.00", "0.50", "6.25", "10.00", 0),
    ("anthropic", "claude-opus-4-7", "5.00", "25.00", "0.50", "6.25", "10.00", 0),
    ("anthropic", "claude-opus-4-8", "5.00", "25.00", "0.50", "6.25", "10.00", 0),
    ("anthropic", "claude-opus-5", "5.00", "25.00", "0.50", "6.25", "10.00", 0),
    ("anthropic", "claude-opus-5-5", "4.00", "20.00", "0.20", "5.00", "8.00", 0),
    ("anthropic", "claude-sonnet-5", "2.00", "10.00", "0.20", "2.50", "4.00", 0),
    ("anthropic", "claude-sonnet-5-5", "2.00", "10.00", "0.20", "2.50", "4.00", 0),
    ("anthropic", "claude-haiku-5-5", "0.10", "0.50", "0.01", "0.125", "0.20", 0),
    ("anthropic", "claude-haiku-5-5", "0.50", "2.50", "0.05", "0.625", "1.00", 100_001),
    ("openai", "gpt-4o", "2.50", "10.00", "1.25", None, None, 0),
    ("openai", "gpt-4o-mini", "0.15", "0.60", "0.075", None, None, 0),
    ("openai", "gpt-4.1", "2.00", "8.00", "0.50", None, None, 0),
    ("openai", "gpt-4.1-mini", "0.40", "1.60", "0.10", None, None, 0),
    ("openai", "o3-mini", "1.10", "4.40", "0.55", None, None, 0),
    ("openai", "o4-mini", "1.10", "4.40", "0.275", None, None, 0),
]

# Gemini rows of the 2026-10-16 snapshot (spec §9.2): pattern, input, output, cache read, tier.
GEMINI_ROWS: list[tuple[str, str, str, str, int]] = [
    ("gemini-2.5-pro", "1.25", "10.00", "0.125", 0),
    ("gemini-2.5-pro", "2.50", "15.00", "0.25", 200_001),
    ("gemini-2.5-flash", "0.30", "2.50", "0.03", 0),
    ("gemini-2.5-flash-lite", "0.10", "0.40", "0.01", 0),
    ("gemini-3-flash-preview", "0.50", "3.00", "0.05", 0),
    ("gemini-3.1-pro-preview", "2.00", "12.00", "0.20", 0),
    ("gemini-3.1-pro-preview", "4.00", "18.00", "0.40", 200_001),
    ("gemini-3.1-flash-lite", "0.25", "1.50", "0.025", 0),
    ("gemini-3.5-flash-lite", "0.30", "2.50", "0.03", 0),
    ("gemini-3.6-flash", "0.75", "3.75", "0.075", 0),
    ("gemini-3.8-flash", "0.75", "3.75", "0.075", 0),
]

# Anthropic rows the 2026-10-15 snapshot lacked (owner's pricing table, 2026-10-15):
# pattern, input, output, cache read, write 5 m, write 1 h.
ADDED_ANTHROPIC_ROWS: list[tuple[str, str, str, str, str, str]] = [
    ("claude-fable-5-1", "10.00", "50.00", "0.25", "12.50", "20.00"),
    ("claude-mythos-5-1", "10.00", "50.00", "0.25", "12.50", "20.00"),
    ("claude-fable-5", "10.00", "50.00", "1.00", "12.50", "20.00"),
    ("claude-mythos-5", "10.00", "50.00", "1.00", "12.50", "20.00"),
    ("claude-opus-4-1", "15.00", "75.00", "1.50", "18.75", "30.00"),
    ("claude-opus-4", "15.00", "75.00", "1.50", "18.75", "30.00"),
    ("claude-sonnet-4", "3.00", "15.00", "0.30", "3.75", "6.00"),
    ("claude-3-5-haiku", "0.80", "4.00", "0.08", "1.00", "1.60"),
]

TIER_MULTIPLIERS = {
    ("anthropic", "batch"): Decimal("0.5"),
    ("anthropic", "priority"): Decimal("1.0"),
    ("openai", "batch"): Decimal("0.5"),
    ("openai", "flex"): Decimal("0.5"),
    ("google", "batch"): Decimal("0.5"),
}


def _as_tuple(
    price: SeedPrice,
) -> tuple[str, str, str, str, str | None, str | None, str | None, int]:
    def text(rate: Decimal | None) -> str | None:
        return None if rate is None else str(rate)

    return (
        price.provider,
        price.model_pattern,
        str(price.input_per_mtok),
        str(price.output_per_mtok),
        text(price.cached_input_per_mtok),
        text(price.cache_write_5m_per_mtok),
        text(price.cache_write_1h_per_mtok),
        price.min_prompt_tokens,
    )


def _rows(snapshot_prices: tuple[SeedPrice, ...], provider: str, pattern: str) -> list[SeedPrice]:
    return [p for p in snapshot_prices if p.provider == provider and p.model_pattern == pattern]


def test_snapshots_are_ordered_and_frozen() -> None:
    assert (*HISTORICAL_SNAPSHOTS, CURRENT_SNAPSHOT) == ALL_SNAPSHOTS
    assert HISTORICAL_SNAPSHOTS == (SNAPSHOT_2026_10_01, SNAPSHOT_2026_10_15)
    dates = [snapshot.effective_from for snapshot in ALL_SNAPSHOTS]
    assert dates == sorted(dates) and len(set(dates)) == len(dates)
    # A released snapshot never learns a later rate.
    assert all(
        price.cache_write_5m_per_mtok is None
        and price.cache_write_1h_per_mtok is None
        and price.min_prompt_tokens == 0
        for price in SNAPSHOT_2026_10_01.prices
    )
    # Multipliers and fees arrived with 2026-10-16; the released snapshots carry none.
    for snapshot in HISTORICAL_SNAPSHOTS:
        assert not snapshot.tier_multipliers
        assert not snapshot.commitment_billed
        assert snapshot.fees == ()


def test_2026_10_15_snapshot_is_frozen() -> None:
    assert SNAPSHOT_2026_10_15.version == "2026-10-15"
    assert SNAPSHOT_2026_10_15.effective_from == datetime(2026, 10, 15, tzinfo=UTC)
    assert [_as_tuple(price) for price in SNAPSHOT_2026_10_15.prices] == ROWS_2026_10_15


def test_2026_10_16_adds_gemini_fees_and_multipliers() -> None:
    assert PRICING_VERSION == CURRENT_SNAPSHOT.version == "2026-10-16"
    assert EFFECTIVE_FROM == CURRENT_SNAPSHOT.effective_from == datetime(2026, 10, 16, tzinfo=UTC)
    prices = CURRENT_SNAPSHOT.prices

    # Every 2026-10-15 row carries over, except the Sonnet 5.5 cache-read correction.
    carried = [_as_tuple(price) for price in prices]
    for row in ROWS_2026_10_15:
        expected = row
        if row[1] == "claude-sonnet-5-5":
            expected = (*row[:4], "0.10", *row[5:])
        assert expected in carried, row
    (sonnet_55,) = _rows(prices, "anthropic", "claude-sonnet-5-5")
    assert sonnet_55.cached_input_per_mtok == Decimal("0.10")

    # Gemini: no write rates (cache storage is billed per hour, not per write).
    google = [
        (
            p.model_pattern,
            str(p.input_per_mtok),
            str(p.output_per_mtok),
            str(p.cached_input_per_mtok),
            p.min_prompt_tokens,
        )
        for p in prices
        if p.provider == "google"
    ]
    assert google == GEMINI_ROWS
    assert all(
        p.cache_write_5m_per_mtok is None and p.cache_write_1h_per_mtok is None
        for p in prices
        if p.provider == "google"
    )
    for pattern in ("gemini-2.5-pro", "gemini-3.1-pro-preview"):
        tiers = sorted(p.min_prompt_tokens for p in _rows(prices, "google", pattern))
        assert tiers == [0, 200_001], pattern

    # The Anthropic models the 2026-10-15 snapshot lacked, with their exact five rates.
    for pattern, input_rate, output_rate, cached, write_5m, write_1h in ADDED_ANTHROPIC_ROWS:
        (price,) = _rows(prices, "anthropic", pattern)
        assert (
            str(price.input_per_mtok),
            str(price.output_per_mtok),
            str(price.cached_input_per_mtok),
            str(price.cache_write_5m_per_mtok),
            str(price.cache_write_1h_per_mtok),
            price.min_prompt_tokens,
        ) == (input_rate, output_rate, cached, write_5m, write_1h, 0), pattern
    assert not _rows(prices, "anthropic", "claude-haiku-3-5")

    assert CURRENT_SNAPSHOT.fees == (
        SeedFee("anthropic", "web_search", Decimal("10.00")),
        SeedFee("openai", "web_search", Decimal("10.00")),
    )
    assert CURRENT_SNAPSHOT.tier_multipliers == TIER_MULTIPLIERS
    assert ("openai", "priority") not in CURRENT_SNAPSHOT.tier_multipliers
    assert CURRENT_SNAPSHOT.commitment_billed == frozenset({("anthropic", "priority")})


async def test_sync_writes_every_snapshot_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # The session fixture already synced; a second sync finds every row present.
    async with session_factory() as db:
        assert await sync_seed_prices(db) == 0
        book = await load_price_book(db)
    expected_rows = sum(len(snapshot.prices) for snapshot in ALL_SNAPSHOTS)
    # 2026-10-01 (18), 2026-10-15 (19), 2026-10-16 (19 carried + 8 Anthropic + 11 Gemini).
    assert expected_rows == 18 + 19 + (19 + 8 + 11)
    assert len(book._prices) == expected_rows


async def test_sync_writes_fees_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        assert await sync_seed_prices(db) == 0
        rows = (
            await db.scalars(select(ServerToolFeeRow).order_by(ServerToolFeeRow.provider))
        ).all()
    assert [
        (row.provider, row.tool, row.usd_per_1k_requests, row.effective_from, row.version)
        for row in rows
    ] == [
        ("anthropic", "web_search", Decimal("10.000000"), EFFECTIVE_FROM, "2026-10-16"),
        ("openai", "web_search", Decimal("10.000000"), EFFECTIVE_FROM, "2026-10-16"),
    ]


async def test_span_is_priced_by_the_snapshot_in_force_when_it_started(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        book = await load_price_book(db)

    old = book.cost(
        provider="anthropic",
        model="claude-haiku-4-5",
        at=datetime(2026, 10, 1, tzinfo=UTC),
        **USAGE,
    )
    assert old is not None
    assert old.pricing_version == SNAPSHOT_2026_10_01.version == "2026-10-01"
    # Writes at the input rate: 6423 * 1.00 + 10 * 5.00, the base cost the pilot stored.
    assert old.cost_usd == Decimal("0.00647300")

    new = book.cost(
        provider="anthropic",
        model="claude-haiku-4-5",
        at=datetime(2026, 10, 16, 12, tzinfo=UTC),
        **USAGE,
    )
    assert new is not None
    assert new.pricing_version == PRICING_VERSION == "2026-10-16"
    # 19 uncached * 1.00 + 6404 written (5 m) * 1.25 + 10 * 5.00: what Anthropic billed.
    assert new.cost_usd == Decimal("0.00807400")


async def test_span_before_1016_keeps_1015_cost(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        book = await load_price_book(db)

    result = book.cost(
        provider="anthropic",
        model="claude-haiku-4-5",
        at=datetime(2026, 10, 15, 12, tzinfo=UTC),
        **USAGE,
    )
    assert result is not None
    assert result.pricing_version == SNAPSHOT_2026_10_15.version == "2026-10-15"
    assert result.cost_usd == Decimal("0.00807400")


async def test_loaded_book_prices_web_search_fees(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        book = await load_price_book(db)

    result = book.cost(
        provider="anthropic",
        model="claude-haiku-4-5",
        at=datetime(2026, 10, 16, 12, tzinfo=UTC),
        web_search_requests=3,
        **USAGE,
    )
    before_fees = book.cost(
        provider="anthropic",
        model="claude-haiku-4-5",
        at=datetime(2026, 10, 15, 12, tzinfo=UTC),
        web_search_requests=3,
        **USAGE,
    )
    assert result is not None and result.fee_usd == Decimal("0.03000000")
    assert result.cost_usd == Decimal("0.03807400")
    # No snapshot before 2026-10-16 carries a fee row: the fee is unknown, never zero.
    assert before_fees is not None and before_fees.fee_usd is None
    assert before_fees.cost_usd == Decimal("0.00807400")

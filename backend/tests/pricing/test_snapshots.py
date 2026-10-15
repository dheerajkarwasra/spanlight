"""Every bundled snapshot is synced, so a span is priced by the one in force when it started.

Ingestion accepts spans up to 90 days old and OTLP batches arrive late, so a fresh install must
price a span dated before the current snapshot exactly as an upgraded install does: by the
historical snapshot, under its version.
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.pricing.cost import load_price_book, sync_seed_prices
from app.pricing.prices import (
    ALL_SNAPSHOTS,
    CURRENT_SNAPSHOT,
    HISTORICAL_SNAPSHOTS,
    PRICING_VERSION,
    SNAPSHOT_2026_10_01,
)

# The pilot's 5-minute cache-write call on Claude Haiku 4.5 (D1).
USAGE = {"input_tokens": 6423, "output_tokens": 10, "cached_tokens": 0, "cache_write_tokens": 6404}


def test_snapshots_are_ordered_and_frozen() -> None:
    assert (*HISTORICAL_SNAPSHOTS, CURRENT_SNAPSHOT) == ALL_SNAPSHOTS
    dates = [snapshot.effective_from for snapshot in ALL_SNAPSHOTS]
    assert dates == sorted(dates) and len(set(dates)) == len(dates)
    # A released snapshot never learns a later rate.
    assert all(
        price.cache_write_5m_per_mtok is None
        and price.cache_write_1h_per_mtok is None
        and price.min_prompt_tokens == 0
        for price in SNAPSHOT_2026_10_01.prices
    )


async def test_sync_writes_every_snapshot_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # The session fixture already synced; a second sync finds every row present.
    async with session_factory() as db:
        assert await sync_seed_prices(db) == 0
        book = await load_price_book(db)
    expected_rows = sum(len(snapshot.prices) for snapshot in ALL_SNAPSHOTS)
    assert len(book._prices) == expected_rows


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
        at=datetime(2026, 10, 15, 12, tzinfo=UTC),
        **USAGE,
    )
    assert new is not None
    assert new.pricing_version == PRICING_VERSION == "2026-10-15"
    # 19 uncached * 1.00 + 6404 written (5 m) * 1.25 + 10 * 5.00: what Anthropic billed.
    assert new.cost_usd == Decimal("0.00807400")

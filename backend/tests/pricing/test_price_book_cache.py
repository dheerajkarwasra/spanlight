"""The seed price book is read at most once per TTL in a process; overrides on every load.

The book looks rows up by canonical provider; the lookup must pick exactly the row a scan of
every row picks (`best_price`), for every provider spelling, model, date and prompt size.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.pricing import cost, price_row
from app.pricing.cost import PriceBook, load_price_book
from app.pricing.price_row import OVERRIDE_VERSION_PREFIX, Price, best_price
from app.pricing.prices import ALL_SNAPSHOTS
from tests.helpers import Workspace, create_workspace

USAGE = {"input_tokens": 1000, "output_tokens": 500, "cached_tokens": 0}
SPELLINGS = [None, "", "openai", " OpenAI ", "anthropic", "claude", "google", "gemini"]
SPELLINGS += ["vertex_ai", "azure", "acme"]


async def _statements(engine: AsyncEngine, sessions: Any, org_id: str | None) -> int:
    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        async with sessions() as db:
            await load_price_book(db, None if org_id is None else uuid.UUID(org_id))
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
    return len(statements)


async def test_a_warm_book_reads_only_the_organizations_overrides(
    browser_factory: Any,
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    workspace = await create_workspace(await browser_factory("cache@example.com"))
    async with session_factory() as db:
        await load_price_book(db)  # warm

    assert await _statements(engine, session_factory, workspace.org_id) == 1
    assert await _statements(engine, session_factory, None) == 0


async def _override(workspace: Workspace) -> None:
    response = await workspace.owner.post(
        f"/api/v1/orgs/{workspace.org_id}/price-overrides",
        json={
            "provider": "openai",
            "model_pattern": "gpt-4o-mini",
            "input_per_mtok": "9",
            "output_per_mtok": "9",
            "effective_from": "2020-01-01T00:00:00Z",
        },
    )
    assert response.status_code == 201, response.text


async def test_an_override_prices_the_next_load_of_its_organization_only(
    browser_factory: Any, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    mine = await create_workspace(await browser_factory("mine@example.com"))
    theirs = await create_workspace(await browser_factory("theirs@example.com"))
    async with session_factory() as db:
        await load_price_book(db, uuid.UUID(mine.org_id))  # warm, before the override exists

    await _override(mine)

    at = datetime.now(UTC)
    async with session_factory() as db:
        own = (await load_price_book(db, uuid.UUID(mine.org_id))).cost(
            provider="openai", model="gpt-4o-mini", at=at, **USAGE
        )
        other = (await load_price_book(db, uuid.UUID(theirs.org_id))).cost(
            provider="openai", model="gpt-4o-mini", at=at, **USAGE
        )
        seed = (await load_price_book(db)).cost(
            provider="openai", model="gpt-4o-mini", at=at, **USAGE
        )
    assert own is not None and own.pricing_version is not None
    assert own.pricing_version.startswith(OVERRIDE_VERSION_PREFIX)
    assert own.cost_usd == Decimal("0.01350000")  # 1500 tokens at 9 per million
    assert other == seed
    assert other is not None and other.pricing_version is not None
    assert not other.pricing_version.startswith(OVERRIDE_VERSION_PREFIX)


def _corpus_book(seed: PriceBook) -> PriceBook:
    """The seed rows plus overrides under an alias, a canonical name and an unknown provider."""
    first = ALL_SNAPSHOTS[0].effective_from
    overrides = [
        Price(
            "gemini",
            "gemini-2.5-pro",
            Decimal(1),
            Decimal(2),
            None,
            first,
            "override:1",
            source="override",
        ),
        Price(
            "openai", "gpt-4o", Decimal(3), Decimal(4), None, first, "override:2", source="override"
        ),
        Price(
            "acme", "acme-1", Decimal(5), Decimal(6), None, first, "override:3", source="override"
        ),
    ]
    return PriceBook([*seed._prices, *overrides], fees=seed.fees)


async def test_the_provider_index_picks_the_row_a_full_scan_picks(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db:
        book = _corpus_book(await load_price_book(db))
    rows = book._prices
    patterns = sorted({row.model_pattern for row in rows})
    models = [m for p in patterns for m in (p, f"{p}-20250101", f"{p}-latest", p.upper())]
    models += ["unknown-model", "gpt-4.1-nano"]
    dates = sorted({row.effective_from for row in rows})
    dates = [dates[0].replace(year=dates[0].year - 1), *dates, datetime.now(UTC)]
    checked = 0
    for provider in SPELLINGS:
        for model in models:
            for at in dates:
                for prompt in (0, 300_000):
                    assert book.find(provider, model, at, prompt) == best_price(
                        rows, provider, model, at, prompt
                    ), (provider, model, at, prompt)
                    checked += 1
    assert checked > 10_000


def test_a_model_is_matched_against_the_rows_once(monkeypatch: Any) -> None:
    """A batch prices the same few models on every span; the names a book keeps are bounded."""
    at = datetime(2026, 11, 1, tzinfo=UTC)
    models = ["gpt-4o-mini", *(f"gpt-other-{index}" for index in range(30))]
    book = PriceBook(
        Price("openai", model, Decimal(1), Decimal(2), None, at, "2026-11-01") for model in models
    )
    first = book.find("openai", "gpt-4o-mini", at)
    assert first is not None
    matched: list[str] = []
    real = price_row.matches_model

    def counting(pattern: str, model: str) -> bool:
        matched.append(pattern)
        return real(pattern, model)

    monkeypatch.setattr(price_row, "matches_model", counting)
    assert book.find("openai", "gpt-4o-mini", at) is first
    # Only the model's own candidate row, not every row of the provider.
    assert matched == ["gpt-4o-mini"]

    for index in range(cost._MATCHING_MODELS + 10):
        book.find("openai", f"unknown-{index}", at)
    assert len(book._matching) <= cost._MATCHING_MODELS
    assert book.find("openai", "gpt-4o-mini", at) is first

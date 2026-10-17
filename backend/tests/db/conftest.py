"""The schema tests rebuild the price tables after each test.

Several tests here downgrade the schema and upgrade it again. A downgrade below 0508 drops the
price tables' cache-write rate columns and deletes their tier rows; the upgrade puts the columns
back empty, and `sync_seed_prices` keeps an existing row as it is, so the seed rows would stay
hollow for the rest of the session and every later price book would price writes at the input
rate. A downgrade below 0600 drops `server_tool_fees` altogether, and the upgrade recreates it
empty. Both tables are small, so they are simply emptied and seeded again.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.pricing.cost import sync_seed_prices


@pytest.fixture(autouse=True)
async def rebuild_price_table(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    yield
    async with session_factory() as db:
        await db.execute(text("DELETE FROM model_prices"))
        await db.execute(text("DELETE FROM server_tool_fees"))
        await sync_seed_prices(db)
        await db.commit()

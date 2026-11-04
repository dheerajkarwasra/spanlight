"""`PostgresTokenBucket.acquire(cost=n)`: `n` tokens taken at once, for the per-project span rate.

`cost=1` is the default and leaves every existing limiter as it was. Two takes that together pass
the burst never both succeed, whatever the interleaving, since the upsert serialises on the row.
"""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.ratelimit import PostgresTokenBucket

BUCKET = PostgresTokenBucket(rate=2, burst=1000)


async def _take(
    session_factory: async_sessionmaker[AsyncSession], key: str, cost: int
) -> float | None:
    async with session_factory() as db, db.begin():
        return await BUCKET.acquire(db, key, cost=cost)


async def test_two_takes_of_600_refuse_the_second_with_the_shortfall_wait(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    assert await _take(session_factory, "cost:pair", 600) is None

    retry_after = await _take(session_factory, "cost:pair", 600)

    # 400 tokens left, 200 short, refilling at 2 a second: at least 100 s (snapshot lag only adds).
    assert retry_after is not None
    assert retry_after >= 200 / BUCKET.rate - 0.5


async def test_cost_one_is_the_default(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as db, db.begin():
        for _ in range(3):
            assert await BUCKET.acquire(db, "cost:one") is None
        assert await BUCKET.acquire(db, "cost:one", cost=997) is None
        assert await BUCKET.acquire(db, "cost:one", cost=1) is not None


async def test_concurrent_takes_never_both_pass_the_burst(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    results = await asyncio.gather(
        *(_take(session_factory, "cost:race", 600) for _ in range(2)),
        _take(session_factory, "cost:race", 600),
    )

    assert sum(result is None for result in results) == 1


@pytest.mark.parametrize("cost", [0, -1, 1001])
async def test_a_cost_outside_one_to_burst_is_refused_loudly(
    session_factory: async_sessionmaker[AsyncSession], cost: int
) -> None:
    async with session_factory() as db, db.begin():
        with pytest.raises(ValueError, match="cost"):
            await BUCKET.acquire(db, "cost:bad", cost=cost)

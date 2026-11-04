"""A cache hit costs no provider spend, so the gateway serves it before the budget guard and
without a reservation: a firing `block` budget still serves a hit and refuses a miss, a hit
leaves the budget's counter and reservations as they were, and a hit is read-only, so a lock
on its row does not hold it up.
"""

import asyncio
from decimal import Decimal

from sqlalchemy import text

from app.db.rls import bypass_rls
from tests.budgets.clock import next_pass
from tests.budgets.support import (
    CHAT_BODY,
    BudgetRef,
    BudgetWorld,
    chat,
    create_budget,
    evaluate,
    ingest_spend,
)
from tests.gateway_support import drain_recorder

OTHER_BODY = {**CHAT_BODY, "messages": [{"role": "user", "content": "Something else"}]}


async def _replay_answers(world: BudgetWorld) -> None:
    """Give the workspace's key a cache TTL, so a repeated request is a hit."""
    key = f"{world.project_url}/gateway/keys/{world.workspace.gateway_key_id}"
    edited = await world.workspace.owner.patch(key, json={"cache_ttl_seconds": 600})
    assert edited.status_code == 200, edited.text


async def _counter(world: BudgetWorld, budget: BudgetRef) -> tuple[Decimal, Decimal]:
    async with world.sessions() as db:
        await bypass_rls(db)
        row = (
            await db.execute(
                text("SELECT settled_usd, reserved_usd FROM budget_counters WHERE budget_id = :id"),
                {"id": budget.id},
            )
        ).one()
    return Decimal(row.settled_usd), Decimal(row.reserved_usd)


async def _reservations(world: BudgetWorld) -> int:
    async with world.sessions() as db:
        await bypass_rls(db)
        count = await db.scalar(
            text("SELECT count(*) FROM budget_reservations WHERE project_id = :project_id"),
            {"project_id": world.workspace.project_id},
        )
    return int(count or 0)


async def test_a_firing_block_budget_serves_a_hit_and_refuses_a_miss(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    await _replay_answers(world)
    budget = await create_budget(world)
    warm = await chat(world, scripted_tokens=100)  # admitted, reserved, settled, cached
    assert (warm.status_code, warm.headers["x-spanlight-cache"]) == (200, "miss")
    await drain_recorder()
    assert await _reservations(world) == 1
    await ingest_spend(world)  # $0.002 against the $0.001 cap
    await evaluate(world, next_pass(world.clock))
    before = await _counter(world, budget)

    hit = await chat(world)
    miss = await world.workspace.gw.post("/gw/v1/chat/completions", json=OTHER_BODY)

    assert (hit.status_code, hit.headers["x-spanlight-cache"]) == (200, "hit")
    assert hit.json() == warm.json()
    assert miss.status_code == 402, miss.text
    assert miss.json()["error"]["spanlight_code"] == "BUDGET_EXCEEDED"
    assert len(world.fake.requests) == 1
    await drain_recorder()
    assert await _reservations(world) == 1  # the hit reserved nothing
    assert await _counter(world, budget) == before


async def test_a_hit_is_not_held_up_by_a_lock_on_its_row(budget_world: BudgetWorld) -> None:
    world = budget_world
    await _replay_answers(world)
    warm = await chat(world, scripted_tokens=100)
    assert (warm.status_code, warm.headers["x-spanlight-cache"]) == (200, "miss")

    async with world.sessions() as db:
        await bypass_rls(db)
        locked = await db.execute(
            text("SELECT cache_key FROM gateway_cache WHERE project_id = :project_id FOR UPDATE"),
            {"project_id": world.workspace.project_id},
        )
        assert len(locked.all()) == 1
        hit = await asyncio.wait_for(chat(world), timeout=10)

    assert (hit.status_code, hit.headers["x-spanlight-cache"]) == (200, "hit")
    assert len(world.fake.requests) == 1

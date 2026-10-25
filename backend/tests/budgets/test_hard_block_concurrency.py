"""Concurrent gateway calls cannot pass a `block` budget together (ADR 0017), the reaper's
worst case yields to a late settle, and a free call reserves nothing.

Twenty calls arrive at once, half on each of two api replicas (two apps on one database), for
a budget sized for five of them. The guard reserves each call's estimate before forwarding, so
exactly five reach the provider and the rest are refused with 402 before any upstream call. The
provider holds its answers until every refusal is out, so no admitted call settles (and frees
its reservation) while the others are still being checked. Once the five settle, the counter
holds no reservation and its settled spend is the real cost of five calls, within the cap plus
one estimate's slack.
"""

import asyncio
from decimal import ROUND_UP, Decimal
from typing import Any

import httpx
from prometheus_client import REGISTRY
from sqlalchemy import text

from app.budgets.jobs import reap_expired
from app.budgets.reserve import RESERVATION_LEASE, estimate
from app.config import Settings
from app.db.rls import bypass_rls
from app.pricing.cost import load_price_book
from tests.budget_fixtures import app_on_clock
from tests.budgets.support import (
    CHAT_BODY,
    PRICED_MODEL,
    BudgetWorld,
    chat_completion,
    create_budget,
)
from tests.gateway_fixtures import TEST_ORIGIN
from tests.gateway_support import ManualClock, Stall, drain_recorder
from tests.helpers import bearer

CALLS = 20
FITS = 5
BODY = {**CHAT_BODY, "max_tokens": 1000}
PROMPT_TOKENS = 100  # $0.00004 of `PRICED_MODEL`: each admitted call settles far below its estimate
RESERVE_METRIC = "spanlight_budget_reserve_seconds"


async def test_twenty_concurrent_calls_admit_five_on_a_budget_sized_for_five(
    budget_world: BudgetWorld, gateway_settings: Settings, app_clock: ManualClock
) -> None:
    world = budget_world
    async with world.sessions() as db:
        one_call = estimate(
            BODY, [(None, PRICED_MODEL)], await load_price_book(db), at=world.clock.wall()
        )
    assert one_call is not None
    # The API takes four decimals: the smallest amount five calls fit in, and six do not.
    amount = (FITS * one_call).quantize(Decimal("0.0001"), rounding=ROUND_UP)
    assert FITS * one_call <= amount < (FITS + 1) * one_call
    budget = await create_budget(world, amount=amount)
    gate = asyncio.Event()
    for _ in range(FITS):
        world.fake.reply_json(chat_completion(PROMPT_TOKENS), before_headers=Stall(gate))
    reserve_count_before = REGISTRY.get_sample_value(f"{RESERVE_METRIC}_count") or 0.0
    reserve_sum_before = REGISTRY.get_sample_value(f"{RESERVE_METRIC}_sum") or 0.0

    async with (
        app_on_clock(gateway_settings, world.fake, app_clock) as replica,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=replica),
            base_url=TEST_ORIGIN,
            headers=bearer(world.workspace.gateway_key),
        ) as second,
    ):
        clients = (world.workspace.gw, second)
        calls = [
            asyncio.create_task(clients[index % 2].post("/gw/v1/chat/completions", json=BODY))
            for index in range(CALLS)
        ]
        # Refusals answer at once; the admitted calls wait at the provider until the gate opens.
        pending = set(calls)
        while len(pending) > FITS:
            done, pending = await asyncio.wait(
                pending, timeout=5, return_when=asyncio.FIRST_COMPLETED
            )
            if not done:
                break  # a stuck call: open the gate and let the assertions say what happened
        gate.set()
        responses = await asyncio.gather(*calls)

    assert sorted(response.status_code for response in responses) == (
        [200] * FITS + [402] * (CALLS - FITS)
    )
    assert len(world.fake.requests) == FITS
    refused = next(response for response in responses if response.status_code == 402)
    error = refused.json()["error"]
    assert error["spanlight_code"] == "BUDGET_EXCEEDED"
    assert "would be exceeded" in error["message"]
    assert "max_tokens" in error["message"]
    assert "soft limit" not in error["message"]

    await drain_recorder()
    async with world.sessions() as db:
        await bypass_rls(db)
        settled, reserved = (
            await db.execute(
                text("SELECT settled_usd, reserved_usd FROM budget_counters WHERE budget_id = :id"),
                {"id": budget.id},
            )
        ).one()
        held = await db.scalar(
            text("SELECT count(*) FROM budget_reservations WHERE budget_id = :id"),
            {"id": budget.id},
        )
    assert reserved == 0
    assert held == 0
    assert settled == FITS * Decimal("0.00004")
    assert settled <= amount + one_call

    count = (REGISTRY.get_sample_value(f"{RESERVE_METRIC}_count") or 0.0) - reserve_count_before
    total = (REGISTRY.get_sample_value(f"{RESERVE_METRIC}_sum") or 0.0) - reserve_sum_before
    assert count == CALLS
    within = {
        le: (REGISTRY.get_sample_value(f"{RESERVE_METRIC}_bucket", {"le": le}) or 0.0)
        for le in ("0.005", "0.01", "0.02", "0.05")
    }
    print(f"reserve: {CALLS} calls at once, mean {total / count * 1000:.2f} ms, within {within}")


async def _counter(world: BudgetWorld, budget_id: Any) -> tuple[Decimal, Decimal, list[str]]:
    """The budget's settled and reserved spend, and the status of each reservation row."""
    async with world.sessions() as db:
        await bypass_rls(db)
        counter = (
            await db.execute(
                text("SELECT settled_usd, reserved_usd FROM budget_counters WHERE budget_id = :id"),
                {"id": budget_id},
            )
        ).first()
        statuses = list(
            await db.scalars(
                text("SELECT status FROM budget_reservations WHERE budget_id = :id"),
                {"id": budget_id},
            )
        )
    return (
        (counter[0], counter[1], statuses)
        if counter is not None
        else (Decimal(0), Decimal(0), statuses)
    )


async def test_a_reaped_reservation_is_corrected_by_its_late_settle(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    budget = await create_budget(world, amount=Decimal("0.01"))
    gate = asyncio.Event()
    world.fake.reply_json(chat_completion(PROMPT_TOKENS), before_headers=Stall(gate))
    call = asyncio.create_task(world.workspace.gw.post("/gw/v1/chat/completions", json=BODY))
    while not world.fake.requests:  # the reservation is committed before the upstream call
        await asyncio.wait({call}, timeout=0.01)
    settled, reserved, statuses = await _counter(world, budget.id)
    assert (settled, reserved > 0, statuses) == (Decimal(0), True, ["held"])

    # The lease runs out while the provider is still answering: the estimate is counted.
    reaped, held = await reap_expired(world.sessions, now=world.clock.wall() + RESERVATION_LEASE)
    assert (reaped, held) == (1, 0)
    assert await _counter(world, budget.id) == (reserved, Decimal(0), ["reaped"])

    gate.set()
    assert (await call).status_code == 200
    await drain_recorder()
    assert await _counter(world, budget.id) == (Decimal("0.00004"), Decimal(0), [])


async def test_a_free_call_reserves_nothing(budget_world: BudgetWorld) -> None:
    """A $0 price override makes the estimate zero: no reservation row (its amount must be
    positive), and the call is admitted."""
    world = budget_world
    budget = await create_budget(world, amount=Decimal("0.01"))
    free = await world.workspace.owner.post(
        f"/api/v1/orgs/{world.workspace.org_id}/price-overrides",
        json={
            "provider": "openai",
            "model_pattern": PRICED_MODEL,
            "input_per_mtok": "0",
            "output_per_mtok": "0",
            "effective_from": "2025-01-01T00:00:00Z",
        },
    )
    assert free.status_code == 201, free.text
    world.fake.reply_json(chat_completion(PROMPT_TOKENS))

    response = await world.workspace.gw.post("/gw/v1/chat/completions", json=BODY)

    assert response.status_code == 200, response.text
    await drain_recorder()
    assert await _counter(world, budget.id) == (Decimal(0), Decimal(0), [])

"""A `block` budget blocks once the alert evaluator has seen the spend, not before (ADR 0012).

The gateway's guard reads the state the evaluation job wrote; it never sums spend per call. Calls
over the cap still go through until the next pass, so a budget can overrun, and the overrun
grows with every pass that does not run. Blocked calls reach no provider, cost nothing and leave
the budget firing. Ingestion never asks the guard.
"""

from datetime import timedelta
from decimal import Decimal

import httpx

from app.core.observability import BUDGET_BLOCKS
from tests.budgets.clock import next_pass
from tests.budgets.support import (
    CAP,
    STEP_COST,
    STEP_TOKENS,
    BudgetWorld,
    blocked_spans,
    chat,
    create_budget,
    evaluate,
    events_of,
    firing_budget,
    guard_decision,
    ingest_spend,
    message,
    outbox_events,
    state_of,
)
from tests.gateway_support import metric_delta

CALLS_PER_INTERVAL = 5
INTERVAL = timedelta(minutes=1)


def assert_blocked(response: httpx.Response) -> None:
    """A 402 `BUDGET_EXCEEDED` in the envelope of the surface called, with no attempt made."""
    assert response.status_code == 402, response.text
    assert response.headers["x-spanlight-attempts"] == "0"
    assert response.headers["x-spanlight-code"] == "BUDGET_EXCEEDED"
    body = response.json()
    if "messages" in str(response.request.url):
        assert body["type"] == "error"
        assert body["error"]["type"] == "billing_error"
    else:
        assert (body["error"]["type"], body["error"]["code"]) == (
            "insufficient_quota",
            "insufficient_quota",
        )
    assert body["error"]["spanlight_code"] == "BUDGET_EXCEEDED"


async def test_block_budget_is_enforced_only_after_evaluation(budget_world: BudgetWorld) -> None:
    world = budget_world
    budget = await create_budget(world)
    await ingest_spend(world)  # $0.002 against a $0.001 cap

    # Not evaluated yet: the call goes through, the overrun ADR 0012 allows.
    assert (await chat(world, scripted_tokens=0)).status_code == 200

    await evaluate(world, next_pass(world.clock))
    state = await state_of(world, budget)
    assert state is not None
    assert (state["state"], state["last_value"]) == ("firing", STEP_COST)
    assert [event["event"] for event in await events_of(world, budget)] == ["budget.exceeded"]
    assert await outbox_events(world) == ["budget.exceeded"]

    blocks = metric_delta(BUDGET_BLOCKS)
    assert_blocked(await chat(world))
    assert_blocked(await message(world))
    assert blocks.value == 2
    assert len(world.fake.requests) == 1  # only the call made before the evaluation
    blocked = await blocked_spans(world)
    assert [row["attributes"]["spanlight.budget.id"] for row in blocked] == [str(budget.id)] * 2
    assert [row["cost_usd"] for row in blocked] == [None, None]
    await ingest_spend(world)  # ingestion is never blocked


async def test_overrun_lasts_until_the_next_evaluation_pass(budget_world: BudgetWorld) -> None:
    world = budget_world
    budget = await create_budget(world)
    await evaluate(world, next_pass(world.clock))  # nothing spent yet: evaluated, not firing

    # Each call spends $0.002: the first crosses the cap, and nothing stops the rest yet.
    allowed = [await chat(world, scripted_tokens=STEP_TOKENS) for _ in range(CALLS_PER_INTERVAL)]
    assert [response.status_code for response in allowed] == [200] * CALLS_PER_INTERVAL

    await evaluate(world, next_pass(world.clock))
    state = await state_of(world, budget)
    assert state is not None and state["state"] == "firing"
    assert state["last_value"] == CALLS_PER_INTERVAL * STEP_COST > CAP

    refused = [await chat(world) for _ in range(CALLS_PER_INTERVAL)]
    for response in refused:
        assert_blocked(response)
    assert len(world.fake.requests) == CALLS_PER_INTERVAL
    assert refused[0].json()["error"]["message"] == (
        'Blocked by a Spanlight budget: Budget "Daily cap" is exhausted: '
        "$0.01 of $0.0010 spent today (soft limit, measured every 60 s)."
    )


async def test_overrun_grows_when_evaluation_is_late(budget_world: BudgetWorld) -> None:
    """Two passes do not run: three intervals of calls go through, not one. The 60 s in the
    docs is the evaluation interval, not a bound on how long a budget can overrun."""
    world = budget_world
    budget = await create_budget(world)
    await evaluate(world, next_pass(world.clock))

    intervals = 3  # the passes after the first two never run
    for _ in range(intervals):
        for _ in range(CALLS_PER_INTERVAL):
            assert (await chat(world, scripted_tokens=STEP_TOKENS)).status_code == 200
        world.clock.advance(INTERVAL.total_seconds())

    # Time passing alone never blocks: two minutes on, with no pass, the guard still allows.
    later = world.clock.wall() + 2 * INTERVAL
    assert (await guard_decision(world, later)).allowed
    world.clock.advance((later - world.clock.wall()).total_seconds())

    await evaluate(world, next_pass(world.clock))
    state = await state_of(world, budget)
    assert state is not None and state["state"] == "firing"
    overrun: Decimal = state["last_value"] - CAP
    one_interval_overrun = CALLS_PER_INTERVAL * STEP_COST - CAP
    assert overrun == intervals * CALLS_PER_INTERVAL * STEP_COST - CAP > one_interval_overrun
    assert_blocked(await chat(world))


async def test_ingestion_is_never_blocked_by_a_firing_budget(budget_world: BudgetWorld) -> None:
    world = budget_world
    await firing_budget(world)
    assert_blocked(await chat(world))

    await ingest_spend(world, steps=3)  # asserts 200 and every span accepted


async def test_blocked_calls_add_no_spend_and_the_budget_stays_firing(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    budget, _ = await firing_budget(world)
    before = await state_of(world, budget)
    assert before is not None

    for _ in range(5):
        assert_blocked(await chat(world))
    await evaluate(world, next_pass(world.clock))

    after = await state_of(world, budget)
    assert after is not None
    assert (after["state"], after["since"]) == ("firing", before["since"])
    assert after["last_value"] == before["last_value"] == STEP_COST
    assert after["last_evaluated_at"] > before["last_evaluated_at"]
    assert [event["event"] for event in await events_of(world, budget)] == ["budget.exceeded"]
    assert await outbox_events(world) == ["budget.exceeded"]
    assert len(await blocked_spans(world)) == 5

"""Where a `block` budget's two refusals come from (ADR 0012 and ADR 0017).

Spend the gateway did not make (the SDK's) blocks only once the alert evaluator has measured it:
the guard reads the state the evaluation job wrote and never sums spans per call, so SDK spend
over the cap still lets calls through until the next pass. Spend the gateway made itself is
settled into the budget's counter with the span, and the next call's estimate is checked against
it before any upstream call, so gateway spend blocks before any pass. Blocked calls reach no
provider, cost nothing and leave the budget firing. Ingestion never asks the guard.
"""

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
    ingest_spend,
    message,
    outbox_events,
    state_of,
)
from tests.gateway_support import drain_recorder, metric_delta


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

    blocks = metric_delta(BUDGET_BLOCKS, reason="firing")
    assert_blocked(await chat(world))
    assert_blocked(await message(world))
    assert blocks.value == 2
    assert len(world.fake.requests) == 1  # only the call made before the evaluation
    blocked = await blocked_spans(world)
    assert [row["attributes"]["spanlight.budget.id"] for row in blocked] == [str(budget.id)] * 2
    assert [row["cost_usd"] for row in blocked] == [None, None]
    await ingest_spend(world)  # ingestion is never blocked


async def test_gateway_spend_blocks_before_the_next_evaluation_pass(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    budget = await create_budget(world)
    await evaluate(world, next_pass(world.clock))  # nothing spent yet: evaluated, not firing

    # The first call fits and spends $0.002, over the cap; its span settles that into the counter.
    assert (await chat(world, scripted_tokens=STEP_TOKENS)).status_code == 200
    await drain_recorder()

    blocks = metric_delta(BUDGET_BLOCKS, reason="reserved")
    refused = await chat(world)
    assert_blocked(refused)
    assert blocks.value == 1
    assert len(world.fake.requests) == 1
    assert refused.json()["error"]["message"] == (
        'Blocked by a Spanlight budget: Budget "Daily cap" would be exceeded: $0.0020 spent or '
        "in flight plus $0.0001 for this call is over the $0.0010 limit today; a lower "
        "max_tokens lowers the reservation."
    )
    state = await state_of(world, budget)
    assert state is not None and state["state"] == "ok"  # no pass has measured it yet

    await evaluate(world, next_pass(world.clock))
    state = await state_of(world, budget)
    assert state is not None and state["state"] == "firing"
    assert state["last_value"] == STEP_COST > CAP
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

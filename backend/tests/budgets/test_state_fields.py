"""A budget's state shows the gateway counter: what is reserved in flight and what settled.

The counter (ADR 0017) is what admits gateway calls, so the Budgets page needs its figures to
show an "in flight" amount and to stop calling a gateway-only cap a soft limit. Only `block`
budgets have a counter: a `notify` budget reads null for both, never a fake `"0"`.
"""

from decimal import Decimal

from tests.budgets.clock import next_pass
from tests.budgets.support import (
    STEP_COST,
    STEP_TOKENS,
    BudgetWorld,
    chat,
    create_budget,
    evaluate,
)
from tests.gateway_support import drain_recorder


async def _state(world: BudgetWorld, budget_id: str) -> dict[str, object]:
    response = await world.workspace.owner.get(f"{world.project_url}/budgets/{budget_id}")
    assert response.status_code == 200, response.text
    state = response.json()["state"]
    assert state is not None
    return dict(state)


async def test_block_budget_state_carries_the_gateway_counter(budget_world: BudgetWorld) -> None:
    world = budget_world
    budget = await create_budget(world, amount=Decimal(1))
    response = await chat(world, scripted_tokens=STEP_TOKENS)
    assert response.status_code == 200, response.text
    await drain_recorder()
    await evaluate(world, next_pass(world.clock))

    state = await _state(world, str(budget.id))
    assert state["gateway_settled_usd"] == str(STEP_COST)
    assert state["reserved_usd"] == "0"

    listed = await world.workspace.owner.get(f"{world.project_url}/budgets")
    [row] = listed.json()
    assert row["state"]["gateway_settled_usd"] == str(STEP_COST)


async def test_notify_budget_state_has_no_counter(budget_world: BudgetWorld) -> None:
    world = budget_world
    budget = await create_budget(world, amount=Decimal(1), action="notify")
    await evaluate(world, next_pass(world.clock))

    state = await _state(world, str(budget.id))
    assert (state["reserved_usd"], state["gateway_settled_usd"]) == (None, None)

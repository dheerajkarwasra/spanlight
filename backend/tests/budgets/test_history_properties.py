"""Public-API state sequences preserve ten ten-cent charges across budget creation/evaluation."""

import uuid
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.budgets.clock import next_pass
from tests.budgets.support import BudgetWorld, chat, create_budget, evaluate
from tests.gateway_support import drain_recorder


@given(
    historical_calls=st.integers(min_value=0, max_value=8),
    evaluation_after=st.lists(st.booleans(), min_size=10, max_size=10),
)
@settings(
    max_examples=8, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
async def test_creation_and_evaluation_order_preserve_one_dollar_allowance(
    budget_world: BudgetWorld, historical_calls: int, evaluation_after: list[bool]
) -> None:
    world = budget_world
    # Each generated example has its own UTC period and budget; fixtures deliberately remain
    # live so every transition still crosses the real API, PostgreSQL, and recorder.
    world.clock.advance(24 * 60 * 60)
    before = len(world.fake.requests)
    for _ in range(historical_calls):
        assert (await chat(world, scripted_tokens=250_000)).status_code == 200
        await drain_recorder()
    budget = await create_budget(world, amount=Decimal("1"), name=f"sequence {uuid.uuid4()}")
    try:
        for index in range(historical_calls, 10):
            if evaluation_after[index]:
                await evaluate(world, next_pass(world.clock))
            assert (await chat(world, scripted_tokens=250_000)).status_code == 200
            await drain_recorder()
        assert (await chat(world, scripted_tokens=250_000)).status_code == 402
        assert len(world.fake.requests) - before == 10
    finally:
        response = await world.workspace.owner.delete(f"{world.project_url}/budgets/{budget.id}")
        assert response.status_code == 204, response.text

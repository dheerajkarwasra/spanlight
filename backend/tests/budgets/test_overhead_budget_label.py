"""Gateway overhead is recorded apart for calls that reserved against a budget and calls that
did not, so the budget's own cost reads from one histogram instead of two independent p95s."""

from prometheus_client import REGISTRY

from tests.budgets.support import BudgetWorld, chat, create_budget


def _overhead_count(budget: str) -> float:
    labels = {"surface": "chat_completions", "budget": budget}
    return REGISTRY.get_sample_value("spanlight_gateway_overhead_seconds_count", labels) or 0.0


async def test_overhead_is_labelled_by_whether_the_call_reserved(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    without, with_ = _overhead_count("without"), _overhead_count("with")

    plain = await chat(world, scripted_tokens=100)
    assert plain.status_code == 200, plain.text
    assert (_overhead_count("without") - without, _overhead_count("with") - with_) == (1, 0)

    await create_budget(world)
    budgeted = await chat(world, scripted_tokens=100)
    assert budgeted.status_code == 200, budgeted.text
    assert (_overhead_count("without") - without, _overhead_count("with") - with_) == (1, 1)

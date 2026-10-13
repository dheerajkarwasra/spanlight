"""A project `block` budget stops a playground run before the provider is reached.

The playground runs the gateway on the app's clock, so the test runs on the pinned day of the
budget fixtures (`tests/budget_fixtures.py`) like the other budget tests.
"""

from tests.budgets.support import BudgetWorld, firing_budget
from tests.evals.experiments import MODEL
from tests.gateway_support import read_gateway_spans


async def test_playground_surfaces_budget_exceeded(budget_world: BudgetWorld) -> None:
    world = budget_world
    await firing_budget(world)
    workspace = world.workspace

    response = await workspace.owner.post(
        f"{world.project_url}/playground/runs",
        json={
            "configs": [
                {
                    "credential_id": str(workspace.openai_credential_id),
                    "model": MODEL,
                    "params": {"max_tokens": 16},
                    "source": {"messages": [{"role": "user", "content": "Hello"}]},
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    (result,) = response.json()["results"]
    assert result["error"] == "BUDGET_EXCEEDED"
    assert result["output"] is None and result["usage"] is None and result["cost_usd"] is None
    assert world.fake.requests == []
    # The refused call is still a trace of its own, and the link in the answer opens it.
    spans = await read_gateway_spans(world.sessions, workspace.project_id)
    (blocked,) = [span for span in spans if span["span_id"] == result["span_id"]]
    assert blocked["trace_id"] == result["trace_id"]
    assert blocked["status"] == "error"
    assert blocked["attributes"]["spanlight.budget.blocked"] is True

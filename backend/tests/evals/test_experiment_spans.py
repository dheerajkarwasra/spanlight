"""An experiment item links to its trace only when the answered call's span was stored."""

from typing import Any

import pytest

from app.evals.experiment_run import execute_run
from app.gateway import call
from tests.evals.experiments import create_experiment, exact_usage, queue_run, reply
from tests.evals.judges import JudgeWorld
from tests.gateway_support import read_gateway_spans


async def test_experiment_item_has_no_trace_link_when_its_span_cannot_be_built(
    judge_world: JudgeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The provider answered (and may have billed), but the gateway could not build the call's
    # span, so nothing was stored. The item keeps its answer; its trace id is null rather than
    # pointing at a trace that does not exist (`require_span=True` on the answered path).
    world = judge_world
    experiment = await create_experiment(world.workspace, items=1, max_cost_usd="1.00")
    run_id = await queue_run(world.workspace, experiment)
    reply(world.fake, exact_usage())

    def no_span(*_: Any, **__: Any) -> dict[str, Any]:
        raise ValueError("span could not be built")

    monkeypatch.setattr(call, "build_span", no_span)

    await execute_run(world.deps, run_id, world.workspace.project_id)

    items = await world.workspace.owner.get(f"{experiment.url}/runs/{run_id}/items")
    assert items.status_code == 200, items.text
    [item] = items.json()["items"]
    assert item["error"] is None and item["output"]["text"] == "ok"
    assert item["trace_id"] is None
    assert len(world.fake.requests) == 1
    assert await read_gateway_spans(world.sessions, world.workspace.project_id) == []

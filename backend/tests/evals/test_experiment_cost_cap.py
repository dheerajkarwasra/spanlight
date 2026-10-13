"""An experiment run's cost cap counts the calls in flight, not only the ones that finished.

Before each call the run checks `spent + the estimates of the calls in flight + this call's
estimate <= max_cost_usd`. Here the cap fits two calls' estimates but not three, and each answer
costs exactly its estimate, so the outcome does not depend on how the calls interleave.
"""

import asyncio
from decimal import Decimal
from typing import Any

from app.evals.experiment_run import COST_CAP_REACHED, execute_run
from tests.evals.experiments import (
    call_estimate,
    create_experiment,
    exact_usage,
    queue_run,
    reply,
)
from tests.evals.judges import JudgeWorld
from tests.gateway_support import Stall

CAP = "0.0040"
ITEMS = 5


async def _requests_seen(world: JudgeWorld, count: int) -> None:
    async with asyncio.timeout(10):
        while len(world.fake.requests) < count:  # noqa: ASYNC110 - the fake has no arrival event
            await asyncio.sleep(0.01)


async def test_experiment_cost_cap_counts_in_flight(judge_world: JudgeWorld) -> None:
    world = judge_world
    estimate = await call_estimate(world.sessions, world.clock)
    assert 2 * estimate <= Decimal(CAP) < 3 * estimate
    experiment = await create_experiment(world.workspace, items=ITEMS, max_cost_usd=CAP)
    run_id = await queue_run(world.workspace, experiment)
    # The first two calls are held at the provider. Nothing has finished, so a cap that counted
    # only finished spend would let the next two items start at once; the in-flight estimates
    # make the third wait.
    release = asyncio.Event()
    for _ in range(2):
        reply(world.fake, exact_usage(), hold=Stall(release))
    run = asyncio.create_task(
        execute_run(world.deps, run_id, world.workspace.project_id), name="experiment-run"
    )
    try:
        await _requests_seen(world, 2)
        assert not run.done()
    finally:
        release.set()
    await asyncio.wait_for(run, timeout=10)

    # A call the cap should have held back would have reached the provider by now (the fake
    # records each request on arrival). Both finished at exactly their estimates (2E), so a
    # third (3E) still does not fit.
    assert len(world.fake.requests) == 2
    stored = await world.workspace.owner.get(f"{experiment.url}/runs/{run_id}")
    assert stored.status_code == 200, stored.text
    body: dict[str, Any] = stored.json()
    assert (body["status"], body["error"]) == ("failed", COST_CAP_REACHED)
    assert Decimal(body["totals"]["cost_usd"]) == 2 * estimate
    items = await world.workspace.owner.get(f"{experiment.url}/runs/{run_id}/items")
    assert len(items.json()["items"]) == 2

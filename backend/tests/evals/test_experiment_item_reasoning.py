"""An experiment item shows why its judge scored it the way it did.

Failure mode: the judge's reasoning is stored as the comment of the score on the item's trace,
but the run's items list returns only the score values, so the reasoning never reaches a reader.
"""

from app.evals.experiment_run import execute_run
from tests.evals.experiments import create_experiment, exact_usage, queue_run, reply
from tests.evals.judges import JudgeWorld, answer, create_judge, estimate_usage


async def test_experiment_item_exposes_judge_reasoning(judge_world: JudgeWorld) -> None:
    world = judge_world
    judge = await create_judge(world, budget="1")
    experiment = await create_experiment(
        world.workspace, items=1, max_cost_usd="1.0", evaluator_ids=[judge.evaluator_id]
    )
    run_id = await queue_run(world.workspace, experiment)
    reply(world.fake, exact_usage())
    answer(world.fake, estimate_usage(judge))
    await execute_run(world.deps, run_id, world.workspace.project_id)

    listed = await world.workspace.owner.get(f"{experiment.url}/runs/{run_id}/items")
    assert listed.status_code == 200, listed.text
    [item] = listed.json()["items"]
    assert item["evaluator_error"] is None and item["scores"]
    assert item["score_comments"] == {name: "On topic." for name in item["scores"]}

"""A run's totals count the items whose scoring broke.

Failure mode: an item whose evaluator failed keeps its answer and records `evaluator_error`, but
the totals count only call errors, so a score tile reads as if every answered item was scored.
Totals stored before the count existed lack it; reads count it from the run's items instead of
reporting 0.
"""

import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.db.rls import bypass_rls
from app.evals import runner
from app.evals.experiment_run import execute_run
from tests.evals.experiments import (
    ExperimentRef,
    create_experiment,
    exact_usage,
    queue_run,
    reply,
)
from tests.evals.judges import JudgeWorld, create_judge


async def _run_with_broken_scoring(
    world: JudgeWorld, monkeypatch: pytest.MonkeyPatch, items: int
) -> tuple[ExperimentRef, uuid.UUID]:
    """An experiment run of `items` items whose evaluator raises on every item."""
    judge = await create_judge(world, budget="1")

    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("scoring defect")

    monkeypatch.setattr(runner, "evaluate_item", broken)
    experiment = await create_experiment(
        world.workspace, items=items, max_cost_usd="1.0", evaluator_ids=[judge.evaluator_id]
    )
    run_id = await queue_run(world.workspace, experiment)
    for _ in range(items):
        reply(world.fake, exact_usage())
    await execute_run(world.deps, run_id, world.workspace.project_id)
    return experiment, run_id


async def _get(world: JudgeWorld, url: str) -> Any:
    response = await world.workspace.owner.get(url)
    assert response.status_code == 200, response.text
    return response.json()


async def test_totals_count_evaluator_errors(
    judge_world: JudgeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    experiment, run_id = await _run_with_broken_scoring(judge_world, monkeypatch, items=2)

    totals = (await _get(judge_world, f"{experiment.url}/runs/{run_id}"))["totals"]
    assert (totals["items"], totals["errors"], totals["evaluator_errors"]) == (2, 0, 2)


async def test_old_totals_without_key_count_evaluator_errors_from_items(
    judge_world: JudgeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = judge_world
    experiment, run_id = await _run_with_broken_scoring(world, monkeypatch, items=1)
    async with world.sessions() as db, db.begin():
        await bypass_rls(db)
        # Totals as a run stored them before the count existed.
        await db.execute(
            text("UPDATE experiment_runs SET totals = totals - 'evaluator_errors' WHERE id = :id"),
            {"id": run_id},
        )

    run = await _get(world, f"{experiment.url}/runs/{run_id}")
    [listed] = (await _get(world, f"{experiment.url}/runs"))["items"]
    got = await _get(world, experiment.url)
    [row] = (await _get(world, experiment.url.rsplit("/", 1)[0]))["items"]
    for totals in (
        run["totals"],
        listed["totals"],
        got["last_run"]["totals"],
        row["last_run"]["totals"],
    ):
        assert totals["evaluator_errors"] == 1

"""An experiment run scores the items that existed when it started and stores a copy of each.

The run reads its item ids once, in the transaction that starts it (spec D5), and every row it
stores, error rows included, holds the item's input, expected output and metadata as the run read
them (spec D1). The mid-run cases read one item per page and pause the run at its first lease
heartbeat: the first item is read and not yet sent, and no other item has been read, so the
dataset can change under a run in flight.
"""

import asyncio
import dataclasses
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import text

from app.db.rls import bypass_rls
from app.evals import experiment_run
from app.evals.experiment_run import execute_run
from tests.evals.experiments import (
    MODEL,
    ExperimentRef,
    create_experiment,
    exact_usage,
    queue_run,
    reply,
)
from tests.evals.judges import JudgeWorld

CAP = "1.0"


async def _dataset(world: JudgeWorld, experiment: ExperimentRef) -> tuple[str, list[Any]]:
    """The experiment's dataset URL and its items, in id order."""
    stored = await world.workspace.owner.get(experiment.url)
    project = f"/api/v1/projects/{world.workspace.project_id}"
    url = f"{project}/datasets/{stored.json()['dataset_id']}"
    listed = await world.workspace.owner.get(f"{url}/items")
    assert listed.status_code == 200, listed.text
    return url, listed.json()["items"]


async def _rows(world: JudgeWorld, run_id: uuid.UUID) -> dict[str, dict[str, Any]]:
    """The run's stored items by dataset item id."""
    async with world.sessions() as db:
        await bypass_rls(db)
        rows = await db.execute(
            text(
                "SELECT dataset_item_id, input, expected_output, item_metadata, error "
                "FROM experiment_items WHERE run_id = :id"
            ),
            {"id": run_id},
        )
        return {str(row["dataset_item_id"]): dict(row) for row in rows.mappings()}


async def _run(world: JudgeWorld, experiment: ExperimentRef, run_id: uuid.UUID) -> Any:
    stored = await world.workspace.owner.get(f"{experiment.url}/runs/{run_id}")
    assert stored.status_code == 200, stored.text
    return stored.json()


async def _run_paused(
    world: JudgeWorld,
    run_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
    change: Callable[[], Awaitable[None]],
) -> None:
    """Execute the run, applying `change` while it is paused before its first call."""
    monkeypatch.setattr(experiment_run, "PAGE_SIZE", 1)
    paused, resume = asyncio.Event(), asyncio.Event()

    async def heartbeat() -> None:
        paused.set()
        await resume.wait()

    deps = dataclasses.replace(world.deps, heartbeat=heartbeat)
    run = asyncio.create_task(execute_run(deps, run_id, world.workspace.project_id))
    try:
        await asyncio.wait_for(paused.wait(), timeout=10)
        await change()
    finally:
        resume.set()
    await asyncio.wait_for(run, timeout=10)


def _assert_copies(rows: dict[str, dict[str, Any]], items: list[Any]) -> None:
    for item in items:
        row = rows[item["id"]]
        copy = (row["input"], row["expected_output"], row["item_metadata"])
        assert copy == (item["input"], item["expected_output"], item["metadata"])


async def test_run_stores_the_copy_it_scored(judge_world: JudgeWorld) -> None:
    world = judge_world
    experiment = await create_experiment(world.workspace, items=2, max_cost_usd=CAP)
    dataset, items = await _dataset(world, experiment)
    first = f"{dataset}/items/{items[0]['id']}"
    edited = await world.workspace.owner.patch(
        first, json={"expected_output": {"answer": "old"}, "metadata": {"tag": "a"}}
    )
    assert edited.status_code == 200, edited.text
    run_id = await queue_run(world.workspace, experiment)
    for _ in items:
        reply(world.fake, exact_usage())
    await execute_run(world.deps, run_id, world.workspace.project_id)

    _, scored = await _dataset(world, experiment)
    _assert_copies(await _rows(world, run_id), scored)
    # Editing the item afterwards does not change what the run stored.
    later = await world.workspace.owner.patch(first, json={"expected_output": "new"})
    assert later.status_code == 200, later.text
    assert (await _rows(world, run_id))[items[0]["id"]]["expected_output"] == {"answer": "old"}


async def test_error_rows_store_the_copy_too(judge_world: JudgeWorld) -> None:
    world = judge_world
    workspace = world.workspace
    experiment = await create_experiment(workspace, items=2, max_cost_usd=CAP)
    dataset, items = await _dataset(world, experiment)
    edited = await workspace.owner.patch(
        f"{dataset}/items/{items[0]['id']}", json={"expected_output": "an answer"}
    )
    assert edited.status_code == 200, edited.text
    project = f"/api/v1/projects/{workspace.project_id}"
    template = {"kind": "text", "text": "Write about {{topic}}."}
    prompt = await workspace.owner.post(
        f"{project}/prompts", json={"name": "copies", "kind": "text", "template": template}
    )
    assert prompt.status_code == 201, prompt.text
    target = {
        "kind": "prompt_version",
        "prompt_version_id": prompt.json()["latest"]["id"],
        "credential_id": str(workspace.openai_credential_id),
        "model": MODEL,
    }
    created = await workspace.owner.post(
        f"{project}/experiments",
        json={
            "name": "missing-variables",
            "dataset_id": dataset.rsplit("/", 1)[1],
            "config": {"target": target, "max_cost_usd": CAP},
        },
    )
    assert created.status_code == 201, created.text
    prompted_id = created.json()["id"]
    prompted = ExperimentRef(uuid.UUID(prompted_id), f"{project}/experiments/{prompted_id}")
    run_id = await queue_run(workspace, prompted)
    await execute_run(world.deps, run_id, workspace.project_id)

    rows = await _rows(world, run_id)
    assert all(row["error"].startswith("missing_variables") for row in rows.values())
    _, scored = await _dataset(world, experiment)
    _assert_copies(rows, scored)


async def test_item_added_mid_run_is_not_scored(
    judge_world: JudgeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = judge_world
    experiment = await create_experiment(world.workspace, items=2, max_cost_usd=CAP)
    dataset, items = await _dataset(world, experiment)
    run_id = await queue_run(world.workspace, experiment)
    for _ in items:
        reply(world.fake, exact_usage())

    async def add_item() -> None:
        added = await world.workspace.owner.post(
            f"{dataset}/items", json={"items": [{"input": "added mid-run"}]}
        )
        assert added.status_code == 201, added.text

    await _run_paused(world, run_id, monkeypatch, add_item)
    assert set(await _rows(world, run_id)) == {item["id"] for item in items}
    assert (await _run(world, experiment, run_id))["status"] == "done"


async def test_item_deleted_mid_run_is_skipped(
    judge_world: JudgeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = judge_world
    experiment = await create_experiment(world.workspace, items=3, max_cost_usd=CAP)
    dataset, items = await _dataset(world, experiment)
    run_id = await queue_run(world.workspace, experiment)
    for _ in items[:2]:
        reply(world.fake, exact_usage())

    async def delete_last() -> None:
        deleted = await world.workspace.owner.delete(f"{dataset}/items/{items[2]['id']}")
        assert deleted.status_code == 204, deleted.text

    await _run_paused(world, run_id, monkeypatch, delete_last)
    assert set(await _rows(world, run_id)) == {items[0]["id"], items[1]["id"]}
    assert (await _run(world, experiment, run_id))["status"] == "done"


async def test_run_whose_remaining_items_are_all_deleted_ends_done(
    judge_world: JudgeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = judge_world
    experiment = await create_experiment(world.workspace, items=3, max_cost_usd=CAP)
    dataset, items = await _dataset(world, experiment)
    run_id = await queue_run(world.workspace, experiment)
    reply(world.fake, exact_usage())

    async def delete_the_rest() -> None:
        for item in items[1:]:
            deleted = await world.workspace.owner.delete(f"{dataset}/items/{item['id']}")
            assert deleted.status_code == 204, deleted.text

    await _run_paused(world, run_id, monkeypatch, delete_the_rest)
    run = await _run(world, experiment, run_id)
    assert (run["status"], run["totals"]["items"]) == ("done", 1)

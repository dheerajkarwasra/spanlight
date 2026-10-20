"""A locked dataset refuses every item write and its delete, and still serves reads, description
edits and experiment runs (spec D6-D8). Locking and unlocking are idempotent and audited once."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bypass_rls
from tests.evals.experiments import create_experiment, queue_run
from tests.gateway_fixtures import GatewayWorkspace

LOCKED_DETAIL = "This dataset is locked. An admin can unlock it."
TRACE_ID = "0" * 31 + "1"


async def _dataset(workspace: GatewayWorkspace) -> tuple[str, str]:
    """A dataset with one item: its URL and the item's id."""
    project = f"/api/v1/projects/{workspace.project_id}"
    created = await workspace.owner.post(f"{project}/datasets", json={"name": "holdout"})
    assert created.status_code == 201, created.text
    url = f"{project}/datasets/{created.json()['id']}"
    added = await workspace.owner.post(f"{url}/items", json={"items": [{"input": "one"}]})
    assert added.status_code == 201, added.text
    items = await workspace.owner.get(f"{url}/items")
    return url, items.json()["items"][0]["id"]


async def _audit(sessions: async_sessionmaker[AsyncSession], action: str) -> list[Any]:
    async with sessions() as db:
        await bypass_rls(db)
        rows = await db.execute(
            text("SELECT target_id, metadata FROM audit_events WHERE action = :action"),
            {"action": action},
        )
        return list(rows.mappings())


async def test_locked_dataset_refuses_every_item_write(
    gateway_workspace: GatewayWorkspace,
) -> None:
    owner = gateway_workspace.owner
    url, item_id = await _dataset(gateway_workspace)
    locked = await owner.post(f"{url}/lock")
    assert locked.status_code == 200, locked.text
    assert locked.json()["locked_at"] is not None

    upload = {"file": ("items.jsonl", b'{"input": "two"}\n', "application/x-ndjson")}
    writes = [
        await owner.post(f"{url}/items", json={"items": [{"input": "two"}]}),
        await owner.post(f"{url}/items/upload", files=upload),
        await owner.post(f"{url}/items/from-traces", json={"trace_ids": [TRACE_ID]}),
        await owner.patch(f"{url}/items/{item_id}", json={"expected_output": "x"}),
        await owner.delete(f"{url}/items/{item_id}"),
        await owner.delete(url),
    ]
    for response in writes:
        assert response.status_code == 409, (response.request.url, response.text)
        assert response.json()["code"] == "DATASET_LOCKED"
        assert response.json()["detail"] == LOCKED_DETAIL

    stored = await owner.get(url)
    assert stored.json()["item_count"] == 1
    items = await owner.get(f"{url}/items")
    assert items.json()["items"][0]["expected_output"] is None


async def test_locked_dataset_still_allows_reads_description_and_runs(
    gateway_workspace: GatewayWorkspace,
) -> None:
    owner = gateway_workspace.owner
    experiment = await create_experiment(gateway_workspace, items=1, max_cost_usd="1.0")
    stored = await owner.get(experiment.url)
    url = f"/api/v1/projects/{gateway_workspace.project_id}/datasets/{stored.json()['dataset_id']}"
    assert (await owner.post(f"{url}/lock")).status_code == 200

    assert (await owner.get(url)).status_code == 200
    assert (await owner.get(f"{url}/items")).status_code == 200
    edited = await owner.patch(url, json={"description": "frozen holdout"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["description"] == "frozen holdout"
    assert edited.json()["locked_at"] is not None
    await queue_run(gateway_workspace, experiment)


async def test_lock_is_idempotent_and_audited_once(
    gateway_workspace: GatewayWorkspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    owner = gateway_workspace.owner
    url, _ = await _dataset(gateway_workspace)
    dataset_id = url.rsplit("/", 1)[1]

    first = await owner.post(f"{url}/lock")
    second = await owner.post(f"{url}/lock")
    assert first.status_code == second.status_code == 200
    assert first.json()["locked_at"] is not None
    assert second.json()["locked_at"] == first.json()["locked_at"]
    locks = await _audit(session_factory, "dataset.lock")
    assert [(row["target_id"], row["metadata"]) for row in locks] == [
        (dataset_id, {"name": "holdout"})
    ]

    unlocked = await owner.post(f"{url}/unlock")
    assert unlocked.status_code == 200, unlocked.text
    assert unlocked.json()["locked_at"] is None
    again = await owner.post(f"{url}/unlock")
    assert again.status_code == 200
    assert again.json()["locked_at"] is None
    unlocks = await _audit(session_factory, "dataset.unlock")
    assert [(row["target_id"], row["metadata"]) for row in unlocks] == [
        (dataset_id, {"name": "holdout"})
    ]

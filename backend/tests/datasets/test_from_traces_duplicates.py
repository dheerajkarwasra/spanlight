"""Adding from traces skips a trace and span already in the dataset and names the project's other
datasets that hold the trace (spec D9)."""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bypass_rls
from tests.gateway_fixtures import GatewayWorkspace
from tests.rls.factories import create, create_project

TRACE_ID = "a" * 32
SPANS = ("b" * 16, "c" * 16)


async def _trace(sessions: async_sessionmaker[AsyncSession], project_id: uuid.UUID) -> None:
    """One trace with two `llm` spans that both stored an input."""
    async with sessions() as db, db.begin():
        await bypass_rls(db)
        await db.execute(
            text(
                "INSERT INTO traces (project_id, trace_id, started_at, ended_at) "
                "VALUES (:project_id, :trace_id, now(), now())"
            ),
            {"project_id": project_id, "trace_id": TRACE_ID},
        )
        for offset, span_id in enumerate(SPANS):
            await db.execute(
                text(
                    "INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, "
                    "started_at, ended_at, input, output) VALUES (:project_id, :trace_id, "
                    ":span_id, 'llm', 'call', 'ok', now() + make_interval(secs => :offset), "
                    "now() + make_interval(secs => :offset), '\"question\"', '\"answer\"')"
                ),
                {
                    "project_id": project_id,
                    "trace_id": TRACE_ID,
                    "span_id": span_id,
                    "offset": offset,
                },
            )


async def _dataset(workspace: GatewayWorkspace, name: str) -> tuple[str, str]:
    """A new dataset's id and URL."""
    project = f"/api/v1/projects/{workspace.project_id}"
    created = await workspace.owner.post(f"{project}/datasets", json={"name": name})
    assert created.status_code == 201, created.text
    return created.json()["id"], f"{project}/datasets/{created.json()['id']}"


async def _add(workspace: GatewayWorkspace, url: str, **body: Any) -> dict[str, Any]:
    added = await workspace.owner.post(
        f"{url}/items/from-traces", json={"trace_ids": [TRACE_ID], **body}
    )
    assert added.status_code == 201, added.text
    result: dict[str, Any] = added.json()
    return result


async def test_same_trace_and_span_is_rejected_in_the_same_dataset(
    gateway_workspace: GatewayWorkspace, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _trace(session_factory, gateway_workspace.project_id)
    _, url = await _dataset(gateway_workspace, "holdout")
    first = await _add(gateway_workspace, url)
    assert first["accepted"] == 1

    second = await _add(gateway_workspace, url)
    assert second["accepted"] == 0
    assert second["rejected"] == [{"trace_id": TRACE_ID, "reason": "already in this dataset"}]
    assert second["item_ids"] == []
    stored = await gateway_workspace.owner.get(url)
    assert stored.json()["item_count"] == 1


async def test_same_trace_other_span_is_added(
    gateway_workspace: GatewayWorkspace, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _trace(session_factory, gateway_workspace.project_id)
    _, url = await _dataset(gateway_workspace, "holdout")
    assert (await _add(gateway_workspace, url, span_id=SPANS[0]))["accepted"] == 1

    other = await _add(gateway_workspace, url, span_id=SPANS[1])
    assert other["accepted"] == 1
    assert other["rejected"] == []
    assert other["also_in"] == []
    stored = await gateway_workspace.owner.get(url)
    assert stored.json()["item_count"] == 2


async def test_trace_in_another_dataset_is_added_and_listed(
    gateway_workspace: GatewayWorkspace, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _trace(session_factory, gateway_workspace.project_id)
    holdout_id, holdout = await _dataset(gateway_workspace, "holdout")
    _, tuning = await _dataset(gateway_workspace, "tuning")
    assert (await _add(gateway_workspace, holdout))["also_in"] == []

    added = await _add(gateway_workspace, tuning)
    assert added["accepted"] == 1
    assert added["rejected"] == []
    assert added["also_in"] == [
        {"trace_id": TRACE_ID, "datasets": [{"id": holdout_id, "name": "holdout"}]}
    ]


async def test_another_projects_dataset_is_never_listed(
    gateway_workspace: GatewayWorkspace, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _trace(session_factory, gateway_workspace.project_id)
    async with session_factory() as db, db.begin():
        await bypass_rls(db)
        other = await create(db, "dataset_items", await create_project(db))
        await db.execute(
            text("UPDATE dataset_items SET source_trace_id = :trace_id WHERE id = :id"),
            {"trace_id": TRACE_ID, "id": other["id"]},
        )
    _, url = await _dataset(gateway_workspace, "tuning")

    added = await _add(gateway_workspace, url)
    assert added["accepted"] == 1
    assert added["also_in"] == []

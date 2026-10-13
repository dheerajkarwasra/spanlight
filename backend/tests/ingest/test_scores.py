"""Duplicate scores: the natural key is `(project_id, id)`; a retry is stored once, the same id in
two projects is two scores, and `traces.has_scores` ends up true whichever of score and trace
arrives first (the ingest upsert ORs it in; the worker's sweep heals a lost race).
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bypass_rls
from app.scores.repair import repair_has_scores
from tests.helpers import Workspace, bearer, create_api_key, new_trace_id, span
from tests.ingest.support import another_project, ingest_native

SessionFactory = async_sessionmaker[AsyncSession]


async def _scores_key(workspace: Workspace) -> dict[str, str]:
    """Bearer headers for a key of the workspace's project that may write scores."""
    key = await create_api_key(workspace.owner, workspace.project_id, scopes=["scores:write"])
    return bearer(key["secret"])


async def _send(
    client: httpx.AsyncClient, headers: dict[str, str], scores: list[dict[str, Any]]
) -> dict[str, Any]:
    response = await client.post("/v1/scores", json={"scores": scores}, headers=headers)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def _stored_scores(session_factory: SessionFactory, project_id: str) -> list[dict[str, Any]]:
    async with session_factory() as db:
        await bypass_rls(db)
        rows = await db.execute(
            text(
                "SELECT id, trace_id, name, value, comment FROM scores "
                "WHERE project_id = :project_id ORDER BY created_at, id"
            ),
            {"project_id": project_id},
        )
        return [dict(row) for row in rows.mappings()]


async def _has_scores(
    session_factory: SessionFactory, project_id: str, trace_id: str
) -> bool | None:
    """The trace's flag, or None when the project has no such trace."""
    async with session_factory() as db:
        await bypass_rls(db)
        found = await db.execute(
            text("SELECT has_scores FROM traces WHERE project_id = :p AND trace_id = :t"),
            {"p": project_id, "t": trace_id},
        )
        row = found.first()
        return None if row is None else bool(row[0])


async def test_duplicate_score_id_is_idempotent(
    workspace: Workspace, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    trace_id = new_trace_id()
    assert (await ingest_native(client, workspace, [span(trace_id=trace_id)])).status_code == 200
    headers = await _scores_key(workspace)
    score_id = str(uuid.uuid4())
    first = {"id": score_id, "trace_id": trace_id, "name": "quality", "value": 4, "comment": "a"}
    retry = {**first, "value": 1, "comment": "b"}  # same id, different content

    sent = [await _send(client, headers, [first]), await _send(client, headers, [retry])]
    together = await _send(client, headers, [first, first])

    # Every send is accepted, none is rejected, and the first write is the one kept.
    assert [(body["accepted"], body["rejected"]) for body in sent] == [(1, []), (1, [])]
    assert (together["accepted"], together["rejected"]) == (2, [])
    stored = await _stored_scores(session_factory, workspace.project_id)
    assert [(str(row["id"]), row["value"], row["comment"]) for row in stored] == [
        (score_id, Decimal(4), "a")
    ]


async def test_score_id_collision_across_projects(
    workspace: Workspace, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    other = await another_project(workspace, "Second")
    trace_id = new_trace_id()
    score_id = str(uuid.uuid4())
    for project, value in ((workspace, 1), (other, 5)):
        assert (await ingest_native(client, project, [span(trace_id=trace_id)])).status_code == 200
        body = await _send(
            client,
            await _scores_key(project),
            [{"id": score_id, "trace_id": trace_id, "name": "quality", "value": value}],
        )
        assert (body["accepted"], body["rejected"]) == (1, [])

    # The second project's write neither collided with nor overwrote the first's.
    for project, value in ((workspace, 1), (other, 5)):
        stored = await _stored_scores(session_factory, project.project_id)
        assert [(str(row["id"]), row["value"]) for row in stored] == [(score_id, Decimal(value))]
        assert await _has_scores(session_factory, project.project_id, trace_id) is True


async def test_score_before_trace_sets_has_scores_when_trace_arrives(
    workspace: Workspace, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    trace_id = new_trace_id()
    headers = await _scores_key(workspace)

    body = await _send(client, headers, [{"trace_id": trace_id, "name": "quality", "value": 3}])

    # A score for a trace the project does not have yet is accepted and stored.
    assert (body["accepted"], body["rejected"]) == (1, [])
    assert len(await _stored_scores(session_factory, workspace.project_id)) == 1
    assert await _has_scores(session_factory, workspace.project_id, trace_id) is None

    assert (await ingest_native(client, workspace, [span(trace_id=trace_id)])).status_code == 200

    assert await _has_scores(session_factory, workspace.project_id, trace_id) is True
    # A later span batch for the trace keeps the flag.
    assert (await ingest_native(client, workspace, [span(trace_id=trace_id)])).status_code == 200
    assert await _has_scores(session_factory, workspace.project_id, trace_id) is True


async def test_repair_has_scores_fixes_a_missed_flag(
    workspace: Workspace, client: httpx.AsyncClient, session_factory: SessionFactory
) -> None:
    trace_id = new_trace_id()
    assert (await ingest_native(client, workspace, [span(trace_id=trace_id)])).status_code == 200
    headers = await _scores_key(workspace)
    await _send(client, headers, [{"trace_id": trace_id, "name": "quality", "value": 3}])
    # The lost race: the score is committed, the trace's flag was never set.
    async with session_factory() as db:
        await bypass_rls(db)
        await db.execute(
            text("UPDATE traces SET has_scores = false WHERE project_id = :p"),
            {"p": workspace.project_id},
        )
        await db.commit()
    assert await _has_scores(session_factory, workspace.project_id, trace_id) is False

    async with session_factory() as db:
        repaired = await repair_has_scores(db, datetime.now(UTC))
        await db.commit()

    assert repaired == 1
    assert await _has_scores(session_factory, workspace.project_id, trace_id) is True
    async with session_factory() as db:  # nothing is left to repair
        assert await repair_has_scores(db, datetime.now(UTC)) == 0

"""A database failure in the middle of a batch commits nothing and is never a 2xx.

Each fault hits the request's own session after the spans were upserted and before the commit
(`tests.ingest.faults`). The client sees a 5xx, no row is left behind, and a replay of the same
batch then stores exactly what a batch that never failed stores.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
import pytest
from prometheus_client import REGISTRY
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import ingest as ingest_api
from app.ingest import pipeline
from tests.helpers import (
    Workspace,
    count_project_rows,
    create_api_key,
    create_project,
    new_span_id,
    new_trace_id,
    span,
)
from tests.ingest.faults import (
    Fault,
    admin_libpq_url,
    delete_project,
    inject_after,
    kill_connection,
    operational_error,
    statement_timeout,
)
from tests.ingest.support import ingest_native, telemetry_rows


def _batch() -> list[dict[str, Any]]:
    """Three spans of one trace: a root and two children."""
    trace_id, root_id = new_trace_id(), new_span_id()
    start = datetime.now(UTC) - timedelta(minutes=5)
    return [
        span(trace_id=trace_id, span_id=root_id, start=start),
        span(trace_id=trace_id, parent_span_id=root_id, start=start + timedelta(seconds=1)),
        span(trace_id=trace_id, parent_span_id=root_id, start=start + timedelta(seconds=2)),
    ]


def _ingested() -> float:
    return REGISTRY.get_sample_value("spanlight_ingested_spans_total", {"source": "native"}) or 0.0


async def _control(workspace: Workspace) -> Workspace:
    """A second project in the same organization, for a batch ingested once and cleanly."""
    project_id = await create_project(workspace.owner, workspace.org_id, "Control")
    key = await create_api_key(workspace.owner, project_id)
    return Workspace(workspace.owner, workspace.org_id, project_id, key["secret"])


@pytest.mark.parametrize(
    ("fault", "status", "code", "retry_after"),
    [
        pytest.param(statement_timeout, 503, "SERVICE_UNAVAILABLE", "5", id="statement-timeout"),
        pytest.param(operational_error, 500, "INTERNAL_ERROR", None, id="operational-error"),
    ],
)
async def test_statement_failure_mid_batch_commits_nothing_and_is_not_2xx(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    fault: Fault,
    status: int,
    code: str,
    retry_after: str | None,
) -> None:
    inject_after(monkeypatch, "_upsert_spans", fault)
    batch = _batch()
    before = _ingested()

    failed = await ingest_native(client, workspace, batch)

    assert failed.status_code == status, failed.text
    assert failed.json()["code"] == code
    assert failed.headers.get("retry-after") == retry_after
    assert await telemetry_rows(session_factory, workspace.project_id) == ([], [])
    assert _ingested() == before

    replayed = await ingest_native(client, workspace, batch)
    assert replayed.json() == {"accepted": 3, "rejected": []}
    spans, (trace,) = await telemetry_rows(session_factory, workspace.project_id)
    assert len(spans) == 3
    totals = (trace["span_count"], trace["input_tokens"], trace["output_tokens"])
    # span() carries 1000 input and 500 output tokens; the root repeats its children's usage,
    # so it is a usage rollup and only the children are summed.
    assert totals == (3, 2000, 1000)


async def test_connection_killed_before_commit_leaves_no_partial_trace(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inject_after(monkeypatch, "_upsert_spans", kill_connection)
    batch = _batch()

    failed = await ingest_native(client, workspace, batch)

    # A lost connection is a retryable 503 (the request pool has no pre-ping), never a 500.
    assert failed.status_code == 503, failed.text
    assert failed.json()["code"] == "SERVICE_UNAVAILABLE"
    assert "retry-after" in failed.headers
    assert await telemetry_rows(session_factory, workspace.project_id) == ([], [])

    # The pool replaces the dead connection, and the replay stores what a clean ingest stores.
    control = await _control(workspace)
    assert (await ingest_native(client, control, batch)).status_code == 200
    assert (await ingest_native(client, workspace, batch)).status_code == 200
    assert await telemetry_rows(session_factory, workspace.project_id) == await telemetry_rows(
        session_factory, control.project_id
    )


async def test_replay_after_an_injected_failure_matches_a_clean_ingest(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The last step before the commit: traces, spans and totals were all written.
    inject_after(monkeypatch, "_recompute_rollups", operational_error)
    batch = _batch()
    control = await _control(workspace)

    assert (await ingest_native(client, workspace, batch)).status_code == 500
    assert (await ingest_native(client, workspace, batch)).status_code == 200
    assert (await ingest_native(client, control, batch)).status_code == 200

    spans, traces = await telemetry_rows(session_factory, workspace.project_id)
    assert (spans, traces) == await telemetry_rows(session_factory, control.project_id)
    assert len(spans) == 3 and len(traces) == 1


@pytest.mark.parametrize(
    "step",
    [
        pytest.param("_store", id="before-the-project-is-read"),
        pytest.param("ingest_spans", id="before-the-project-is-locked"),
    ],
)
async def test_project_deleted_between_auth_and_write_is_401_and_writes_nothing(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    step: str,
) -> None:
    original = getattr(ingest_api, step)

    async def delete_first(*args: Any, **kwargs: Any) -> Any:
        await delete_project(workspace.project_id)  # the key was already checked
        return await original(*args, **kwargs)

    monkeypatch.setattr(ingest_api, step, delete_first)

    response = await ingest_native(client, workspace, _batch())

    assert response.status_code == 401, response.text
    assert response.json()["code"] == "UNAUTHORIZED"
    counts = await count_project_rows(session_factory, workspace.project_id)
    assert counts == {"traces": 0, "spans": 0, "api_keys": 0}


async def _wait_for_lock_wait() -> None:
    """Return once the batch's FOR KEY SHARE on the project waits behind the deletion.

    Its own autocommit connection: `pg_stat_activity` is a snapshot taken once per transaction.
    """
    async with await psycopg.AsyncConnection.connect(admin_libpq_url(), autocommit=True) as watch:
        for _ in range(500):
            cursor = await watch.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                "AND query ILIKE '%FOR KEY SHARE%'"
            )
            row = await cursor.fetchone()
            if row is not None and row[0] > 0:
                return
            await asyncio.sleep(0.01)
    raise AssertionError("the batch never waited for the project's lock")


async def test_a_batch_queued_behind_a_project_deletion_is_401_and_writes_nothing(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The deletion holds the project row when the batch asks for it: the batch's FOR KEY SHARE
    # waits, then finds no project, and nothing it wrote can outlive the deletion.
    original = pipeline.ingest_spans

    async def racing_deletion(*args: Any, **kwargs: Any) -> Any:
        async with await psycopg.AsyncConnection.connect(admin_libpq_url()) as admin:
            await admin.execute("DELETE FROM projects WHERE id = %s", (workspace.project_id,))
            batch = asyncio.ensure_future(original(*args, **kwargs))
            await _wait_for_lock_wait()
            await admin.commit()
            return await batch

    monkeypatch.setattr(ingest_api, "ingest_spans", racing_deletion)

    response = await ingest_native(client, workspace, _batch())

    assert response.status_code == 401, response.text
    counts = await count_project_rows(session_factory, workspace.project_id)
    assert counts == {"traces": 0, "spans": 0, "api_keys": 0}

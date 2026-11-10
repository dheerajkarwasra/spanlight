"""The histogram reads closed, clean hours from `trace_rollups_hourly` and every other hour from
`traces`, and returns the raw histogram's rows either way.

A rollup row is made wrong on purpose (`_skew`) to see which source an hour was read from: the
skew shows only where the rollup is trusted.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
import pytest

from app.config import Settings
from app.db.rls import bypass_rls
from app.rollups.retention import delete_hourly_rollups
from tests.attribution.support import SessionFactory
from tests.helpers import Browser, Workspace, bearer, create_workspace, new_trace_id, span
from tests.ingest.faults import admin_libpq_url
from tests.rollups.test_dirty_repair import repair

DAY_START = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
# Yesterday 10:00 UTC, an hour that closed long ago.
BASE = DAY_START - timedelta(days=1) + timedelta(hours=10)
HOUR = timedelta(hours=1)
SKEW = 7

# (offset from BASE, status, environment): two hours of traces, and one outside a mid-hour window.
TRACES = [
    (timedelta(minutes=5), "ok", "prod"),
    (timedelta(minutes=40), "error", "prod"),
    (timedelta(minutes=50), "ok", "staging"),
    (timedelta(hours=2, minutes=1), "ok", "prod"),
    (timedelta(hours=2, minutes=30), "error", "staging"),
]
# Starts inside the first hour: its 10:05 trace is outside, and that hour is only partly read.
WINDOW = {"from": (BASE + timedelta(minutes=20)).isoformat(), "to": (BASE + 23 * HOUR).isoformat()}
WEEK = {"from": (BASE - timedelta(days=6)).isoformat(), "to": (BASE + 23 * HOUR).isoformat()}


@pytest.fixture
async def workspace(browser_factory: Any) -> Workspace:
    owner: Browser = await browser_factory("owner@example.com")
    return await create_workspace(owner)


async def _ingest(client: httpx.AsyncClient, workspace: Workspace, *traces: Any) -> None:
    spans = [
        span(
            trace_id=new_trace_id(), start=BASE + offset, status=status, trace={"environment": env}
        )
        for offset, status, env in traces
    ]
    response = await client.post(
        "/v1/traces", json={"spans": spans}, headers=bearer(workspace.api_key)
    )
    assert response.json()["rejected"] == [], response.text


@pytest.fixture
async def rolled(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: SessionFactory,
    settings: Settings,
) -> Workspace:
    """The traces stored and their hours settled by the real repair job."""
    await _ingest(client, workspace, *TRACES)
    await repair(session_factory, settings)
    async with await psycopg.AsyncConnection.connect(admin_libpq_url()) as admin:
        hours = await (
            await admin.execute(
                "SELECT count(DISTINCT bucket_start), (SELECT count(*) FROM aggregate_dirty_hours "
                "WHERE (pending_consumers & 1) <> 0) FROM trace_rollups_hourly "
                "WHERE project_id = %s",
                (workspace.project_id,),
            )
        ).fetchone()
    assert hours == (2, 0), "the repair job must settle both hours"
    return workspace


async def _admin(sql: str, *params: Any) -> None:
    async with await psycopg.AsyncConnection.connect(admin_libpq_url(), autocommit=True) as admin:
        await admin.execute(sql, params)


async def _skew(workspace: Workspace, hour: datetime) -> None:
    await _admin(
        "UPDATE trace_rollups_hourly SET traces = traces + %s "
        "WHERE project_id = %s AND bucket_start = %s AND environment = 'prod'",
        SKEW,
        workspace.project_id,
        hour,
    )


async def _counts(
    workspace: Workspace, window: dict[str, str], **params: Any
) -> dict[str, tuple[int, int]]:
    response = await workspace.owner.get(
        f"/api/v1/projects/{workspace.project_id}/traces/histogram", params={**window, **params}
    )
    assert response.status_code == 200, response.text
    return {
        bucket["bucket_start"]: (bucket["traces"], bucket["errors"])
        for bucket in response.json()["buckets"]
    }


def _at(hours: int) -> str:
    return (BASE + hours * HOUR).isoformat().replace("+00:00", "Z")


async def test_settled_hours_give_the_same_histogram_as_the_traces(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    await _ingest(client, workspace, *TRACES)
    before = [
        await _counts(workspace, window, **env)
        for window in (WINDOW, WEEK)
        for env in ({}, {"environment": "prod"}, {"environment": "staging"})
    ]
    assert before[0] == {_at(0): (2, 1), _at(2): (2, 1)}
    await repair(session_factory, settings)
    after = [
        await _counts(workspace, window, **env)
        for window in (WINDOW, WEEK)
        for env in ({}, {"environment": "prod"}, {"environment": "staging"})
    ]
    assert after == before


async def test_a_closed_clean_hour_is_read_from_the_rollup_and_a_partial_hour_is_not(
    rolled: Workspace,
) -> None:
    await _skew(rolled, BASE)
    await _skew(rolled, BASE + 2 * HOUR)
    # 10:00 is cut by the window, so its traces are counted; 12:00 is whole, closed and clean.
    assert await _counts(rolled, WINDOW) == {_at(0): (2, 1), _at(2): (2 + SKEW, 1)}
    assert await _counts(rolled, WINDOW, environment="prod") == {
        _at(0): (1, 1),
        _at(2): (1 + SKEW, 0),
    }
    assert await _counts(rolled, WINDOW, environment="staging") == {_at(0): (1, 0), _at(2): (1, 1)}


async def test_any_other_filter_counts_traces(rolled: Workspace) -> None:
    await _skew(rolled, BASE + 2 * HOUR)
    assert await _counts(rolled, WINDOW, status="error") == {_at(0): (1, 1), _at(2): (1, 1)}


async def test_an_hour_written_since_its_rollup_is_counted_from_traces(
    client: httpx.AsyncClient, rolled: Workspace
) -> None:
    await _skew(rolled, BASE + 2 * HOUR)
    await _ingest(client, rolled, (timedelta(hours=2, minutes=45), "error", "prod"))
    assert await _counts(rolled, WINDOW) == {_at(0): (2, 1), _at(2): (3, 2)}


async def test_an_hour_that_lost_source_rows_is_counted_from_traces(rolled: Workspace) -> None:
    await _skew(rolled, BASE + 2 * HOUR)
    await _admin(
        "INSERT INTO aggregate_incomplete_hours (project_id, bucket_hour, reason) "
        "VALUES (%s, %s, 'retention')",
        rolled.project_id,
        BASE + 2 * HOUR,
    )
    assert await _counts(rolled, WINDOW) == {_at(0): (2, 1), _at(2): (2, 1)}


async def test_unverified_history_is_counted_from_traces(rolled: Workspace) -> None:
    await _skew(rolled, BASE + 2 * HOUR)
    await _admin(
        "UPDATE projects SET aggregate_history_unverified_before = %s WHERE id = %s",
        BASE + 5 * HOUR,
        rolled.project_id,
    )
    assert await _counts(rolled, WINDOW) == {_at(0): (2, 1), _at(2): (2, 1)}


WHOLE = {"from": (BASE - HOUR).isoformat(), "to": (BASE + 23 * HOUR).isoformat()}


@pytest.mark.parametrize("batch_size", [1, 1000])
async def test_hours_whose_rollups_retention_removed_are_counted_from_traces(
    rolled: Workspace, session_factory: SessionFactory, batch_size: int
) -> None:
    """Rollup retention runs apart from trace retention, so traces can outlive their hour's rows:
    all of them (a whole batch), or some of an hour's (a batch that ends inside the hour)."""
    truth = {_at(0): (3, 1), _at(2): (2, 1)}
    assert await _counts(rolled, WHOLE) == truth
    async with session_factory() as db:
        await bypass_rls(db)
        deleted = await delete_hourly_rollups(
            db, uuid.UUID(rolled.project_id), BASE + 5 * HOUR, batch_size
        )
        await db.commit()
    assert deleted >= 1
    assert await _counts(rolled, WHOLE) == truth
    assert await _counts(rolled, WHOLE, environment="prod") == {_at(0): (2, 1), _at(2): (1, 0)}

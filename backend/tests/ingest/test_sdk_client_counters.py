"""SDK exporter counters: cumulative `client` totals become per-hour `sdk` losses.

The SDK sends its exporter's lifetime `dropped_total` and `failed_total` with every spans batch.
The server keeps the last totals per exporter and records only the increase, so a batch that is
retried, repeated or reordered never counts a loss twice. A `client` object that fails
validation is ignored as a whole and the batch is still accepted.
"""

import asyncio
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import psycopg
import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import ingest as ingest_api
from app.db.rls import bypass_rls
from app.rollups.compute import floor_hour
from tests.gateway_support import logged
from tests.helpers import Workspace, bearer, new_trace_id, span
from tests.ingest.faults import admin_libpq_url

EXPORTER = "0123456789abcdef0123456789abcdef"


def _client(
    dropped: Any, failed: Any, *, exporter_id: Any = EXPORTER, version: Any = "0.2.0"
) -> dict[str, Any]:
    return {
        "sdk": "spanlight-python",
        "version": version,
        "exporter_id": exporter_id,
        "dropped_total": dropped,
        "failed_total": failed,
    }


async def _post(
    client: httpx.AsyncClient,
    workspace: Workspace,
    counters: Any,
    spans: list[dict[str, Any]] | None = None,
) -> httpx.Response:
    body: dict[str, Any] = {"spans": [span(trace_id=new_trace_id())] if spans is None else spans}
    if counters is not None:
        body["client"] = counters
    response = await client.post("/v1/traces", json=body, headers=bearer(workspace.api_key))
    assert response.status_code == 200, response.text
    return response


async def _sdk_losses(
    session_factory: async_sessionmaker[AsyncSession], project_id: str
) -> dict[str, int]:
    """The project's `sdk` losses summed by reason over every hour, read past RLS."""
    async with session_factory() as db:
        await bypass_rls(db)
        rows = await db.execute(
            text(
                "SELECT reason, sum(count)::bigint FROM telemetry_losses_hourly "
                "WHERE project_id = :p AND source = 'sdk' GROUP BY reason"
            ),
            {"p": project_id},
        )
        return {reason: count for reason, count in rows.all()}


async def _counters(
    session_factory: async_sessionmaker[AsyncSession], project_id: str
) -> list[dict[str, Any]]:
    async with session_factory() as db:
        await bypass_rls(db)
        rows = await db.execute(
            text(
                "SELECT exporter_id, dropped_total, failed_total, sdk, sdk_version, last_seen_at "
                "FROM sdk_exporter_counters WHERE project_id = :p ORDER BY exporter_id"
            ),
            {"p": project_id},
        )
        return [dict(row._mapping) for row in rows]


async def test_first_sighting_counts_both_totals_once(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    before = datetime.now(UTC)
    response = await _post(client, workspace, _client(12, 100))
    after = datetime.now(UTC)

    assert response.json() == {"accepted": 1, "rejected": []}
    assert await _sdk_losses(session_factory, workspace.project_id) == {
        "dropped": 12,
        "failed": 100,
    }
    async with session_factory() as db:
        await bypass_rls(db)
        buckets = (
            await db.scalars(
                text("SELECT DISTINCT bucket_start FROM telemetry_losses_hourly WHERE source='sdk'")
            )
        ).all()
    assert set(buckets) <= {floor_hour(before), floor_hour(after)} and len(buckets) == 1
    [row] = await _counters(session_factory, workspace.project_id)
    assert (row["exporter_id"], row["dropped_total"], row["failed_total"]) == (EXPORTER, 12, 100)
    assert (row["sdk"], row["sdk_version"]) == ("spanlight-python", "0.2.0")
    assert before <= row["last_seen_at"] <= after


async def test_an_equal_repeat_writes_no_loss_and_advances_only_last_seen(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _post(client, workspace, _client(12, 100))
    [first] = await _counters(session_factory, workspace.project_id)
    await asyncio.sleep(0.01)

    await _post(client, workspace, _client(12, 100))

    assert await _sdk_losses(session_factory, workspace.project_id) == {
        "dropped": 12,
        "failed": 100,
    }
    [second] = await _counters(session_factory, workspace.project_id)
    assert second["last_seen_at"] > first["last_seen_at"]
    assert {k: v for k, v in second.items() if k != "last_seen_at"} == {
        k: v for k, v in first.items() if k != "last_seen_at"
    }


async def test_growth_writes_the_delta(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _post(client, workspace, _client(12, 100))
    await _post(client, workspace, _client(15, 100))
    await _post(client, workspace, _client(15, 107))

    assert await _sdk_losses(session_factory, workspace.project_id) == {
        "dropped": 15,
        "failed": 107,
    }
    [row] = await _counters(session_factory, workspace.project_id)
    assert (row["dropped_total"], row["failed_total"]) == (15, 107)


async def test_a_smaller_total_writes_nothing_and_keeps_the_larger_one(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _post(client, workspace, _client(12, 100))
    # An older batch delivered late: the totals went down, which a cumulative counter never does.
    await _post(client, workspace, _client(3, 90))

    assert await _sdk_losses(session_factory, workspace.project_id) == {
        "dropped": 12,
        "failed": 100,
    }
    [row] = await _counters(session_factory, workspace.project_id)
    assert (row["dropped_total"], row["failed_total"]) == (12, 100)

    # Only one counter shrank: the other one's growth is still a loss.
    await _post(client, workspace, _client(10, 104))
    assert await _sdk_losses(session_factory, workspace.project_id) == {
        "dropped": 12,
        "failed": 104,
    }
    [row] = await _counters(session_factory, workspace.project_id)
    assert (row["dropped_total"], row["failed_total"]) == (12, 104)


@pytest.mark.parametrize(
    "counters",
    [
        pytest.param(_client(2**63, 0), id="dropped-total-past-bigint"),
        pytest.param(_client(1, 2**63), id="failed-total-past-bigint"),
        pytest.param(_client(-1, 5), id="negative-dropped-total"),
        pytest.param(_client(5, -1), id="negative-failed-total"),
        pytest.param(_client(True, 5), id="boolean-total"),
        pytest.param(_client("5", 5), id="string-total"),
        pytest.param(_client(1.5, 5), id="fractional-total"),
        pytest.param(_client(5, 5, exporter_id="z" * 32), id="non-hex-exporter-id"),
        pytest.param(_client(5, 5, exporter_id=EXPORTER.upper()), id="uppercase-exporter-id"),
        pytest.param(_client(5, 5, exporter_id=EXPORTER[:31]), id="short-exporter-id"),
        pytest.param(_client(5, 5, exporter_id=EXPORTER + "0"), id="long-exporter-id"),
        pytest.param(_client(5, 5, exporter_id=EXPORTER + "\n"), id="exporter-id-newline"),
        pytest.param(_client(5, 5, version="1" * 33), id="33-character-version"),
        pytest.param(_client(5, 5, version=""), id="empty-version"),
        pytest.param({**_client(5, 5), "sdk": "s" * 65}, id="long-sdk-name"),
        pytest.param({k: v for k, v in _client(5, 5).items() if k != "sdk"}, id="missing-sdk"),
        pytest.param("spanlight-python", id="not-an-object"),
        pytest.param([_client(5, 5)], id="a-list"),
    ],
)
async def test_an_invalid_client_object_is_ignored_and_the_batch_accepted(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    counters: Any,
) -> None:
    response = await _post(client, workspace, counters)

    assert response.json() == {"accepted": 1, "rejected": []}
    assert await _counters(session_factory, workspace.project_id) == []
    assert await _sdk_losses(session_factory, workspace.project_id) == {}


async def test_a_33_character_version_is_ignored_where_32_is_kept(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # The database CHECK allows 32; validation must refuse 33 before the batch's statement does.
    await _post(client, workspace, _client(1, 0, version="1" * 33))
    assert await _counters(session_factory, workspace.project_id) == []

    await _post(client, workspace, _client(1, 0, version="1" * 32))
    [row] = await _counters(session_factory, workspace.project_id)
    assert row["sdk_version"] == "1" * 32


async def test_unknown_client_fields_are_ignored(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _post(client, workspace, {**_client(4, 0), "queue_size": 2048})

    assert await _sdk_losses(session_factory, workspace.project_id) == {"dropped": 4}


async def test_zero_totals_record_nothing(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    counter_statements: list[str],
) -> None:
    await _post(client, workspace, _client(0, 0))

    assert any("INSERT INTO spans" in s for s in counter_statements)  # the listener works
    assert not [s for s in counter_statements if "sdk_exporter_counters" in s]
    assert await _counters(session_factory, workspace.project_id) == []
    assert await _sdk_losses(session_factory, workspace.project_id) == {}


async def test_an_ignored_client_is_named_on_the_ingest_log_line_without_its_values(
    client: httpx.AsyncClient, workspace: Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    await _post(client, workspace, _client(5, 5, exporter_id="SECRET-EXPORTER-VALUE"))
    await _post(client, workspace, None)

    ignored, clean = logged(caplog, "spans_ingested")
    assert ignored["client_ignored"] == ["exporter_id"]
    assert ignored["project_id"] == workspace.project_id
    assert "client_ignored" not in clean
    assert "SECRET-EXPORTER-VALUE" not in caplog.text


async def test_a_thousand_exporters_in_a_thousand_batches(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unlimited(*_: Any, **__: Any) -> None:
        return None

    monkeypatch.setattr(ingest_api, "enforce_rate_limit", unlimited)

    for n in range(1000):
        await _post(client, workspace, _client(n % 3, 1, exporter_id=f"{n:032x}"), spans=[])

    rows = await _counters(session_factory, workspace.project_id)
    assert len(rows) == 1000
    assert await _sdk_losses(session_factory, workspace.project_id) == {
        "dropped": sum(n % 3 for n in range(1000)),
        "failed": 1000,
    }


@pytest.fixture
def counter_statements() -> Iterator[list[str]]:
    """The SQL of every statement any engine runs while the test does."""
    seen: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        seen.append(statement)

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", record)


async def test_a_batch_without_client_runs_no_counter_statement(
    client: httpx.AsyncClient, workspace: Workspace, counter_statements: list[str]
) -> None:
    await _post(client, workspace, None)

    assert any("INSERT INTO spans" in s for s in counter_statements)  # the listener works
    assert not [s for s in counter_statements if "sdk_exporter_counters" in s]
    assert not [s for s in counter_statements if "telemetry_losses_hourly" in s]


async def _wait_for_counter_lock(timeout_s: float = 5.0) -> None:
    """Return once a statement on `sdk_exporter_counters` waits for a lock."""
    async with await psycopg.AsyncConnection.connect(admin_libpq_url(), autocommit=True) as watch:
        for _ in range(int(timeout_s / 0.01)):
            cursor = await watch.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                "AND query LIKE '%sdk_exporter_counters%'"
            )
            row = await cursor.fetchone()
            if row is not None and row[0] > 0:
                return
            await asyncio.sleep(0.01)
    raise AssertionError("the batch never waited on the exporter's row")


async def test_a_concurrent_first_sighting_is_not_counted_twice(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Another request saw this exporter first and has not committed yet. The batch cannot see that
    # row, so it is not a first sighting it may count in full: it records nothing and keeps the
    # other request's totals; the exporter's next report counts any growth.
    async with await psycopg.AsyncConnection.connect(admin_libpq_url()) as other:
        await other.execute(
            "INSERT INTO sdk_exporter_counters (project_id, exporter_id, dropped_total, "
            "failed_total, sdk, sdk_version, last_seen_at) "
            "VALUES (%s, %s, 7, 0, 'spanlight-python', '0.2.0', now())",
            (workspace.project_id, EXPORTER),
        )
        batch = asyncio.ensure_future(_post(client, workspace, _client(7, 0)))
        await _wait_for_counter_lock()
        await other.commit()
        response = await batch

    assert response.json() == {"accepted": 1, "rejected": []}
    assert await _sdk_losses(session_factory, workspace.project_id) == {}
    [row] = await _counters(session_factory, workspace.project_id)
    assert (row["dropped_total"], row["failed_total"]) == (7, 0)

    await _post(client, workspace, _client(9, 0))
    assert await _sdk_losses(session_factory, workspace.project_id) == {"dropped": 2}

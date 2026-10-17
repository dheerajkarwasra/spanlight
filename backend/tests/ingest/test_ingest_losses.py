"""Ingest rejections are counted as `ingest` losses, by reason, in the batch's own transaction."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import ingest as ingest_api
from app.api.window import TimeWindow
from app.db.rls import bind_project, bypass_rls
from app.rollups.compute import floor_hour
from app.telemetry_losses import queries
from app.telemetry_losses.queries import LossRow
from app.telemetry_losses.service import upsert_losses
from tests.helpers import Workspace, bearer, count_project_rows, new_trace_id, span
from tests.ingest.faults import delete_project, operational_error
from tests.ingest.support import (
    another_project,
    ingest_native,
    otlp_json_document,
    otlp_json_span,
)

EXPORTER = "fedcba9876543210fedcba9876543210"


def _with_rejections() -> list[dict[str, Any]]:
    """One good span and three rejected ones: two too old, one without a name."""
    old = datetime.now(UTC) - timedelta(days=200)
    nameless = span(trace_id=new_trace_id())
    del nameless["name"]
    return [
        span(trace_id=new_trace_id()),
        span(trace_id=new_trace_id(), start=old),
        span(trace_id=new_trace_id(), start=old),
        nameless,
    ]


async def _loss_rows(
    session_factory: async_sessionmaker[AsyncSession], project_id: str
) -> list[tuple[datetime, str, str, int]]:
    async with session_factory() as db:
        await bypass_rls(db)
        rows = await db.execute(
            text(
                "SELECT bucket_start, source, reason, count FROM telemetry_losses_hourly "
                "WHERE project_id = :p ORDER BY source, reason"
            ),
            {"p": project_id},
        )
        return [tuple(row) for row in rows.all()]


async def test_rejections_of_two_reasons_write_two_rows_in_the_batch_hour(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    before = datetime.now(UTC)
    response = await ingest_native(client, workspace, _with_rejections())
    after = datetime.now(UTC)

    assert response.status_code == 200, response.text
    assert response.json()["accepted"] == 1 and len(response.json()["rejected"]) == 3
    rows = await _loss_rows(session_factory, workspace.project_id)
    assert [(source, reason, count) for _, source, reason, count in rows] == [
        ("ingest", "too_old", 2),
        ("ingest", "validation", 1),
    ]
    assert {bucket for bucket, *_ in rows} <= {floor_hour(before), floor_hour(after)}
    assert len({bucket for bucket, *_ in rows}) == 1


async def test_a_second_batch_adds_to_the_hour(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await ingest_native(client, workspace, _with_rejections())
    await ingest_native(client, workspace, _with_rejections())

    losses = await _read_as(session_factory, workspace.project_id, workspace.project_id)
    assert losses == [
        LossRow(source="ingest", reason="too_old", count=4),
        LossRow(source="ingest", reason="validation", count=2),
    ]


async def test_otlp_rejections_are_counted_too(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    stale = otlp_json_span()
    old_nanos = int((datetime.now(UTC) - timedelta(days=200)).timestamp() * 1e9)
    stale["startTimeUnixNano"] = str(old_nanos)
    stale["endTimeUnixNano"] = str(old_nanos + 1_000_000)
    response = await client.post(
        "/v1/otlp/traces",
        json=otlp_json_document(otlp_json_span(), stale),
        headers=bearer(workspace.api_key),
    )

    assert response.status_code == 200, response.text
    rows = await _loss_rows(session_factory, workspace.project_id)
    assert [(source, reason, count) for _, source, reason, count in rows] == [
        ("ingest", "too_old", 1)
    ]


async def test_a_clean_batch_writes_no_loss_row(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    response = await ingest_native(client, workspace, [span(trace_id=new_trace_id())])

    assert response.json() == {"accepted": 1, "rejected": []}
    assert await _loss_rows(session_factory, workspace.project_id) == []


def _batch_with_losses() -> dict[str, Any]:
    client = {
        "sdk": "spanlight-python",
        "version": "0.2.0",
        "exporter_id": EXPORTER,
        "dropped_total": 3,
        "failed_total": 4,
    }
    return {"spans": _with_rejections(), "client": client}


async def test_a_project_deleted_mid_batch_leaves_no_loss_row(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = ingest_api.ingest_spans

    async def delete_first(*args: Any, **kwargs: Any) -> Any:
        await delete_project(workspace.project_id)  # the key was already checked
        return await original(*args, **kwargs)  # raises ProjectGoneError

    monkeypatch.setattr(ingest_api, "ingest_spans", delete_first)

    response = await client.post("/v1/traces", json=_batch_with_losses(), headers=_auth(workspace))

    assert response.status_code == 401, response.text
    assert await _loss_rows(session_factory, workspace.project_id) == []
    assert await _all_loss_and_counter_rows(session_factory) == (0, 0)


async def test_a_failure_after_the_losses_are_written_rolls_them_back(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = ingest_api.record_sdk_counters

    async def then_fail(db: AsyncSession, *args: Any, **kwargs: Any) -> None:
        await original(db, *args, **kwargs)
        await operational_error(db)

    monkeypatch.setattr(ingest_api, "record_sdk_counters", then_fail)

    response = await client.post("/v1/traces", json=_batch_with_losses(), headers=_auth(workspace))

    assert response.status_code == 500, response.text
    assert await _all_loss_and_counter_rows(session_factory) == (0, 0)
    counts = await count_project_rows(session_factory, workspace.project_id)
    assert (counts["spans"], counts["traces"]) == (0, 0)


async def test_another_projects_binding_sees_none_of_the_losses(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    other = await another_project(workspace, "Other")
    response = await client.post("/v1/traces", json=_batch_with_losses(), headers=_auth(workspace))
    assert response.status_code == 200, response.text

    # Bound to B, neither A's losses nor A's exporter row are visible, even asking for A by id.
    assert await _read_as(session_factory, other.project_id, workspace.project_id) == []
    assert await _read_as(session_factory, other.project_id, other.project_id) == []
    async with session_factory() as db:
        await bind_project(db, UUID(other.project_id))
        assert await db.scalar(text("SELECT count(*) FROM sdk_exporter_counters")) == 0
        assert await db.scalar(text("SELECT count(*) FROM telemetry_losses_hourly")) == 0

    losses = await _read_as(session_factory, workspace.project_id, workspace.project_id)
    assert losses == [
        LossRow(source="ingest", reason="too_old", count=2),
        LossRow(source="ingest", reason="validation", count=1),
        LossRow(source="sdk", reason="dropped", count=3),
        LossRow(source="sdk", reason="failed", count=4),
    ]


async def test_read_losses_covers_the_hours_that_overlap_the_window(
    workspace: Workspace, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    project_id = UUID(workspace.project_id)
    hour = datetime(2026, 10, 16, 10, tzinfo=UTC)
    async with session_factory() as db:
        await bind_project(db, project_id)
        await upsert_losses(
            db,
            project_id,
            {
                ("gateway", "overflow", hour - timedelta(hours=1)): 1,
                ("gateway", "overflow", hour): 2,
                ("gateway", "overflow", hour + timedelta(hours=1)): 4,
                ("gateway", "error", hour + timedelta(hours=2)): 8,
            },
        )
        await db.commit()

    async with session_factory() as db:
        await bind_project(db, project_id)
        window = TimeWindow(start=hour + timedelta(minutes=30), end=hour + timedelta(hours=2))
        losses = await queries.read_losses(db, project_id, window)
    # 10:00 overlaps [10:30, 12:00) and 11:00 lies inside it; 09:00 and 12:00 are outside.
    assert losses == [LossRow(source="gateway", reason="overflow", count=6)]


def _auth(workspace: Workspace) -> dict[str, str]:
    return bearer(workspace.api_key)


async def _read_as(
    session_factory: async_sessionmaker[AsyncSession], bound: str, project_id: str
) -> list[LossRow]:
    """`read_losses` for `project_id` over the hours around now, in a session bound to `bound`."""
    async with session_factory() as db:
        await bind_project(db, UUID(bound))
        return await queries.read_losses(db, UUID(project_id), _around_now())


def _around_now() -> TimeWindow:
    now = datetime.now(UTC)
    return TimeWindow(start=now - timedelta(hours=2), end=now + timedelta(hours=2))


async def _all_loss_and_counter_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[int, int]:
    async with session_factory() as db:
        await bypass_rls(db)
        losses = await db.scalar(text("SELECT count(*) FROM telemetry_losses_hourly"))
        counters = await db.scalar(text("SELECT count(*) FROM sdk_exporter_counters"))
    return int(losses or 0), int(counters or 0)

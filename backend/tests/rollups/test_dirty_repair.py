"""Durable repairs converge through the registered job and real PostgreSQL transactions."""

import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from prometheus_client import REGISTRY
from sqlalchemy import text

from app.config import Settings
from app.db.rls import bind_project
from app.jobs.context import TaskContext
from app.jobs.queue import claim_next, complete, enqueue
from app.jobs.tasks import TASKS
from app.rollups.coverage import lock_aggregate_sources, mark_trace_source_loss
from app.rollups.dirty import mark_dirty_hours
from tests.attribution.support import SessionFactory, SpanSeed, TraceSeed, seed_traces
from tests.helpers import bearer, create_workspace, new_trace_id, span
from tests.rls.factories import create_project
from tests.rollups.dirty_support import dirty_count, dirty_row, set_dirty_pending


async def repair(factory: SessionFactory, settings: Settings, **flags: bool) -> None:
    async with factory() as db:
        await enqueue(db, "repair_aggregates", max_attempts=1)
        await db.commit()
        job = await claim_next(db)
    assert job is not None
    handler = TASKS.get("repair_aggregates")
    assert handler is not None, "durable aggregate repair must be registered"
    configured = settings.model_copy(update={"aggregate_repair_enabled": True, **flags})
    await handler(TaskContext(job, configured, factory), {})
    async with factory() as db:
        await complete(db, job)


async def test_late_correction_clears_departed_hour_day_and_owner_groups(
    browser_factory: Any,
    client: httpx.AsyncClient,
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    workspace = await create_workspace(await browser_factory("repair-move@example.com"))
    project = uuid.UUID(workspace.project_id)
    old = datetime.now(UTC).replace(hour=23, minute=0, second=0, microsecond=0) - timedelta(days=5)
    new = old + timedelta(hours=2)
    original = span(
        trace_id=new_trace_id(),
        start=old,
        trace={"environment": "prod", "user_id": "old", "release": "v1"},
    )
    response = await client.post(
        "/v1/traces", json={"spans": [original]}, headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200 and response.json()["rejected"] == []
    await repair(session_factory, settings)
    changed = span(
        start=new,
        trace_id=original["trace_id"],
        span_id=original["span_id"],
        model="gpt-4o",
        trace={"environment": "stage", "user_id": "new", "release": "v2"},
    )
    response = await client.post(
        "/v1/traces", json={"spans": [changed]}, headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200 and response.json()["rejected"] == []
    await repair(session_factory, settings)
    async with session_factory() as db:
        await bind_project(db, project)
        assert (
            await db.execute(
                text("SELECT bucket_start, environment, model, span_count FROM span_rollups_hourly")
            )
        ).all() == [(new, "stage", "gpt-4o", 1)]
        assert (
            await db.execute(
                text("SELECT day, external_user_id, environment, traces FROM user_stats_daily")
            )
        ).all() == [(new.date(), "new", "stage", 1)]
        assert (
            await db.scalar(
                text("SELECT count(*) FROM attribution_rollups_daily WHERE day=:day"),
                {"day": old.date()},
            )
            == 0
        )
        assert (
            await db.scalar(
                text("SELECT count(*) FROM cost_rollups_daily WHERE day=:day"), {"day": old.date()}
            )
            == 0
        )
        assert await dirty_count(db) == 0
    await repair(session_factory, settings)


async def test_consumption_off_preserves_pending_and_optional_reenable_finishes(
    browser_factory: Any,
    session_factory: SessionFactory,
    settings: Settings,
) -> None:

    workspace = await create_workspace(await browser_factory("repair-flags@example.com"))
    project = uuid.UUID(workspace.project_id)
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(days=40)
    async with session_factory() as db:
        await bind_project(db, project)
        await mark_dirty_hours(db, project, [hour])
        age = (await dirty_row(db)).first_marked_at
        await db.commit()
    await repair(session_factory, settings, aggregate_repair_enabled=False)
    async with session_factory() as db:
        await bind_project(db, project)
        assert (await dirty_row(db)).pending_consumers == 15
    pending_response = await workspace.owner.get(
        f"/api/v1/projects/{project}/providers/errors/timeseries",
        params={"from": hour.isoformat(), "to": (hour + timedelta(hours=1)).isoformat()},
    )
    assert pending_response.status_code == 200
    assert pending_response.headers["Aggregate-Status"] == "pending"
    assert pending_response.headers["Aggregate-Oldest-Pending-At"]
    await repair(session_factory, settings, attribution_enabled=False, user_stats_enabled=False)
    response = await workspace.owner.get(
        f"/api/v1/projects/{project}/providers/errors/timeseries",
        params={"from": hour.isoformat(), "to": (hour + timedelta(hours=1)).isoformat()},
    )
    assert response.status_code == 200
    assert response.headers["Aggregate-Status"] == "complete"
    assert "Aggregate-Oldest-Pending-At" not in response.headers
    async with session_factory() as db:
        await bind_project(db, project)
        row = await dirty_row(db)
        assert (row.pending_consumers, row.first_marked_at) == (12, age)
    await repair(session_factory, settings, attribution_enabled=True, user_stats_enabled=False)
    async with session_factory() as db:
        await bind_project(db, project)
        assert (await dirty_row(db)).pending_consumers == 8
    await repair(session_factory, settings, attribution_enabled=False, user_stats_enabled=True)
    async with session_factory() as db:
        await bind_project(db, project)
        assert await dirty_count(db) == 0


async def test_complete_hour_in_incomplete_day_preserves_daily_snapshots(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:

    hour = datetime(2026, 10, 20, 10, tzinfo=UTC)
    async with session_factory() as db:
        project = await create_project(db)
        await bind_project(db, project)
        ids = await seed_traces(
            db,
            project,
            {
                "kept": TraceSeed(
                    hour, (SpanSeed("llm", hour, Decimal(1), 10, 2),), external_user_id="u"
                ),
                "lost": TraceSeed(
                    hour + timedelta(hours=2),
                    (SpanSeed("llm", hour + timedelta(hours=2), Decimal(2), 10, 2),),
                    external_user_id="u",
                ),
            },
        )
        await mark_dirty_hours(db, project, [hour, hour + timedelta(hours=2)])
        await db.commit()
    await repair(session_factory, settings)
    async with session_factory() as db:
        await lock_aggregate_sources(db, project)
        await mark_trace_source_loss(db, project, [ids["lost"]], "retention")
        await db.execute(
            text("DELETE FROM traces WHERE project_id=:p AND trace_id=:t"),
            {"p": project, "t": ids["lost"]},
        )
        await mark_dirty_hours(db, project, [hour, hour + timedelta(hours=2)])
        await db.commit()
    await repair(session_factory, settings, attribution_enabled=False, user_stats_enabled=False)
    async with session_factory() as db:
        await bind_project(db, project)
        assert await db.scalar(text("SELECT sum(cost_usd) FROM cost_rollups_daily")) == Decimal(3)
        assert await db.scalar(text("SELECT sum(traces) FROM user_stats_daily")) == 2
        assert await db.scalar(text("SELECT sum(span_count) FROM span_rollups_hourly")) == 2
        assert await dirty_count(db) == 0
        assert await db.scalar(text("SELECT count(*) FROM aggregate_incomplete_hours")) == 1


async def test_disabled_only_rows_do_not_hide_later_runnable_project(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:

    hour = datetime(2026, 10, 20, 10, tzinfo=UTC)
    async with session_factory() as db:
        disabled = await create_project(db)
        await bind_project(db, disabled)
        await mark_dirty_hours(db, disabled, [hour + timedelta(hours=i) for i in range(150)])
        await set_dirty_pending(db, 12)
        healthy = await create_project(db)
        await bind_project(db, healthy)
        await mark_dirty_hours(db, healthy, [hour])
        await db.commit()
    deferred_before = (
        REGISTRY.get_sample_value(
            "spanlight_aggregate_repair_outcomes_total", {"outcome": "deferred"}
        )
        or 0
    )
    await repair(session_factory, settings, attribution_enabled=False, user_stats_enabled=False)
    assert (
        REGISTRY.get_sample_value(
            "spanlight_aggregate_repair_outcomes_total", {"outcome": "deferred"}
        )
        == deferred_before + 1
    )
    async with session_factory() as db:
        await bind_project(db, healthy)
        assert await dirty_count(db) == 1
        assert (await dirty_row(db)).pending_consumers == 12
        await bind_project(db, disabled)
        assert await dirty_count(db) == 150


@pytest.mark.serial
async def test_locked_project_does_not_block_later_project(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:

    hour = datetime(2026, 10, 20, 10, tzinfo=UTC)
    async with session_factory() as db:
        locked = await create_project(db)
        await bind_project(db, locked)
        await mark_dirty_hours(db, locked, [hour, hour + timedelta(hours=1)])
        healthy = await create_project(db)
        await bind_project(db, healthy)
        await mark_dirty_hours(db, healthy, [hour])
        await db.commit()
    async with session_factory() as blocker:
        await lock_aggregate_sources(blocker, locked)
        started = time.monotonic()
        await repair(session_factory, settings)
        assert time.monotonic() - started < 5
    async with session_factory() as db:
        await bind_project(db, healthy)
        assert await dirty_count(db) == 0
        await bind_project(db, locked)
        assert await dirty_count(db) == 2
    await repair(session_factory, settings)
    async with session_factory() as db:
        await bind_project(db, locked)
        assert await dirty_count(db) == 0


async def test_disabled_unavailable_boundary_obligations_are_settled(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    hour = datetime(2026, 10, 20, 10, tzinfo=UTC)
    async with session_factory() as db:
        legacy = await create_project(db)
        await bind_project(db, legacy)
        await db.execute(
            text("UPDATE projects SET aggregate_history_unverified_before=:boundary WHERE id=:p"),
            {"boundary": hour + timedelta(days=1), "p": legacy},
        )
        await mark_dirty_hours(db, legacy, [hour])
        await set_dirty_pending(db, 12)
        unactivated = await create_project(db)
        await bind_project(db, unactivated)
        await db.execute(
            text("UPDATE projects SET aggregate_history_unverified_before=NULL WHERE id=:p"),
            {"p": unactivated},
        )
        await mark_dirty_hours(db, unactivated, [hour])
        await set_dirty_pending(db, 12)
        await db.commit()
    await repair(session_factory, settings, attribution_enabled=False, user_stats_enabled=False)
    async with session_factory() as db:
        await bind_project(db, legacy)
        assert await dirty_count(db) == 0
        assert await db.scalar(text("SELECT count(*) FROM aggregate_incomplete_hours")) == 0
        assert await db.scalar(
            text("SELECT aggregate_history_unverified_before FROM projects WHERE id=:p"),
            {"p": legacy},
        ) == hour + timedelta(days=1)
        await bind_project(db, unactivated)
        assert await dirty_count(db) == 0
        assert (
            await db.scalar(
                text("SELECT aggregate_history_unverified_before FROM projects WHERE id=:p"),
                {"p": unactivated},
            )
            is None
        )

"""The registered retention job serves bounded opportunities over cleanup classes and scopes."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from app.config import Settings
from app.jobs.context import TaskContext
from app.jobs.queue import LeaseLostError
from app.jobs.tasks import retention
from app.jobs.tasks.retention import delete_batch, run_retention
from app.jobs.tasks.retention_cursor import (
    CLASSES,
    GLOBAL_CLASSES,
    PROJECT_CLASSES,
    Opportunity,
    next_opportunity,
)
from tests.attribution.daily_support import queued
from tests.gateway_support import logged
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects, seed_llm_spans

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


async def _context(session_factory: SessionFactory, settings: Settings) -> TaskContext:
    """A real claimed `retention` job: a fresh claim stands for a worker restart."""
    job = await queued(session_factory, "retention", {})
    return TaskContext(
        job=job, settings=settings, session_factory=session_factory, clock=lambda: NOW
    )


async def _reset_cursors(session_factory: SessionFactory) -> None:
    await execute(
        session_factory,
        "UPDATE retention_progress SET attempt_order = 0, after_scope_id = NULL",
        {},
    )


async def _cursors(session_factory: SessionFactory) -> dict[str, dict[str, Any]]:
    rows = await fetch(session_factory, "SELECT * FROM retention_progress", {})
    return {row["class"]: row for row in rows}


async def _expired_projects(session_factory: SessionFactory, count: int) -> list[uuid.UUID]:
    """`count` projects with one-day retention and two ten-day-old traces each."""
    projects = await new_projects(session_factory, count)
    for project in projects:
        await execute(
            session_factory,
            "UPDATE projects SET retention_days = 1 WHERE id = :id",
            {"id": project},
        )
        await seed_llm_spans(session_factory, project, 2, started_at=NOW - timedelta(days=10))
    return projects


async def _trace_count(session_factory: SessionFactory, project: uuid.UUID) -> int:
    [row] = await fetch(
        session_factory,
        "SELECT count(*) AS n FROM traces WHERE project_id = :id",
        {"id": project},
    )
    return int(row["n"])


def _deadline() -> float:
    return asyncio.get_running_loop().time() + 30


async def test_a_locked_project_does_not_block_the_run_or_a_later_project(
    session_factory: SessionFactory, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    first, second = sorted(await _expired_projects(session_factory, 2))
    async with session_factory() as holder:
        # Ingestion holding the first project's traces: retention gives up on that batch
        # (lock timeout), keeps its turn spent, and still reaches the second project.
        await holder.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
        await holder.execute(
            text("SELECT trace_id FROM traces WHERE project_id = :id FOR UPDATE"), {"id": first}
        )
        context = await _context(session_factory, settings)
        # The run bounds itself at 40 s; the old job waited on the lock for ever.
        await asyncio.wait_for(run_retention(context, {}), timeout=45)
        await holder.rollback()
    assert await _trace_count(session_factory, first) == 2
    assert await _trace_count(session_factory, second) == 0
    [summary] = logged(caplog, "retention_applied")
    assert summary["outcomes"]["lock_timeout"] >= 1
    assert summary["deleted"]["trace_cascades"] == 2

    # Released, a later run (a fresh claim: a restart) deletes the first project's traces, and
    # the identity can be ingested again afterwards: retention leaves no tombstone.
    caplog.clear()
    await run_retention(await _context(session_factory, settings), {})
    assert await _trace_count(session_factory, first) == 0
    await seed_llm_spans(session_factory, first, 1, started_at=NOW - timedelta(hours=1))
    assert await _trace_count(session_factory, first) == 1


async def test_every_class_gets_opportunities_and_the_run_stays_bounded(
    session_factory: SessionFactory, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    await _reset_cursors(session_factory)
    [project] = await _expired_projects(session_factory, 1)
    before = await _cursors(session_factory)

    await run_retention(await _context(session_factory, settings), {})

    [summary] = logged(caplog, "retention_applied")
    assert summary["opportunities"] == retention.MAX_OPPORTUNITIES
    assert summary["deleted"]["trace_cascades"] == 2
    assert set(summary["outcomes"]) <= {"deleted", "empty", "no_scope"}
    # Two traces fit one batch and every later batch was empty: nothing was left unreached.
    assert summary["incomplete"] is False
    after = await _cursors(session_factory)
    for cleanup_class in CLASSES:
        assert after[cleanup_class]["attempt_order"] > before[cleanup_class]["attempt_order"]
        assert after[cleanup_class]["last_attempted_at"] is not None
    assert after["trace_cascades"]["after_scope_id"] == project
    # Every class had the same number of turns: the oldest-first order is round robin, so the
    # last round's tokens are 14 consecutive sequence values.
    orders = sorted(row["attempt_order"] for row in after.values())
    assert orders[-1] - orders[0] == len(CLASSES) - 1


async def test_serialized_rotation_visits_every_class_project_pair_within_the_bound(
    session_factory: SessionFactory, settings: Settings
) -> None:
    await _reset_cursors(session_factory)
    projects = set(await new_projects(session_factory, 3))
    seen: set[tuple[str, uuid.UUID | None]] = set()
    for _ in range(len(CLASSES) * len(projects)):
        # Each discovery under its own claimed job: the rotation lives in the database.
        context = await _context(session_factory, settings)
        opportunity = await next_opportunity(context, _deadline())
        assert opportunity is not None
        seen.add((opportunity.cleanup_class, opportunity.scope_id))
    for cleanup_class in PROJECT_CLASSES:
        assert {scope for name, scope in seen if name == cleanup_class} == projects, cleanup_class
    # No billing sources: an empty roster still rotates, with no scope; global classes likewise.
    for cleanup_class in CLASSES:
        if cleanup_class not in PROJECT_CLASSES:
            assert (cleanup_class, None) in seen


async def test_cursor_wraps_and_survives_the_deletion_of_its_project(
    session_factory: SessionFactory, settings: Settings
) -> None:
    await _reset_cursors(session_factory)
    low, high = sorted(await new_projects(session_factory, 2))
    gone = uuid.UUID(int=(low.int + high.int) // 2)  # between the two, never a project
    await execute(
        session_factory,
        "UPDATE retention_progress SET after_scope_id = :cursor, attempt_order = 0 "
        "WHERE class = 'trace_cascades'",
        {"cursor": gone},
    )
    await execute(
        session_factory,
        "UPDATE retention_progress SET attempt_order = 1 WHERE class <> 'trace_cascades'",
        {},
    )
    context = await _context(session_factory, settings)
    first = await next_opportunity(context, _deadline())
    assert first == Opportunity("trace_cascades", high)
    await execute(
        session_factory,
        "UPDATE retention_progress SET attempt_order = 0 WHERE class = 'trace_cascades'",
        {},
    )
    second = await next_opportunity(context, _deadline())
    assert second == Opportunity("trace_cascades", low)  # past the last id: wrap


async def test_a_lost_lease_rolls_the_rotation_back(
    session_factory: SessionFactory, settings: Settings
) -> None:
    await _reset_cursors(session_factory)
    context = await _context(session_factory, settings)
    await execute(
        session_factory, "UPDATE jobs SET fence = fence + 1 WHERE id = :id", {"id": context.job.id}
    )
    before = await _cursors(session_factory)
    with pytest.raises(LeaseLostError):
        await next_opportunity(context, _deadline())
    assert await _cursors(session_factory) == before


async def test_a_lost_lease_rolls_deletion_and_source_markers_back(
    session_factory: SessionFactory, settings: Settings
) -> None:
    [project] = await _expired_projects(session_factory, 1)
    context = await _context(session_factory, settings)
    await execute(
        session_factory,
        "UPDATE jobs SET lease_until = clock_timestamp() - interval '1 millisecond' WHERE id = :id",
        {"id": context.job.id},
    )
    with pytest.raises(LeaseLostError):
        await retention._serve(context, Opportunity("trace_cascades", project), NOW, _deadline())
    assert await _trace_count(session_factory, project) == 2
    assert await fetch(session_factory, "SELECT 1 FROM aggregate_incomplete_hours", {}) == []


async def test_one_giant_trace_cascades_atomically_in_one_opportunity(
    session_factory: SessionFactory,
) -> None:
    [project] = await new_projects(session_factory, 1)
    await execute(
        session_factory, "UPDATE projects SET retention_days = 1 WHERE id = :id", {"id": project}
    )
    started = NOW - timedelta(days=10)
    await execute(
        session_factory,
        "INSERT INTO traces (project_id, trace_id, started_at, ended_at) "
        "VALUES (:p, repeat('a', 32), :s, :s + interval '3 hours')",
        {"p": project, "s": started},
    )
    await execute(
        session_factory,
        "INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, started_at, "
        "ended_at) SELECT :p, repeat('a', 32), lpad(n::text, 16, '0'), 'llm', 'call', 'ok', "
        ":s + (n % 3) * interval '1 hour', :s + (n % 3) * interval '1 hour' + interval '1 second' "
        "FROM generate_series(1, 1000) n",
        {"p": project, "s": started},
    )
    async with session_factory() as db:
        assert await delete_batch(db, "trace_cascades", project, NOW, audit_days=0) == 1
        await db.commit()
    assert await fetch(session_factory, "SELECT 1 FROM spans", {}) == []
    hours = await fetch(
        session_factory,
        "SELECT bucket_hour FROM aggregate_incomplete_hours WHERE project_id = :p "
        "ORDER BY bucket_hour",
        {"p": project},
    )
    assert [row["bucket_hour"] for row in hours] == [started + timedelta(hours=h) for h in range(3)]


async def test_global_classes_need_no_scope_and_audit_keep_all_deletes_nothing(
    session_factory: SessionFactory,
) -> None:
    async with session_factory() as db:
        assert await delete_batch(db, "audit_events", None, NOW, audit_days=0) == 0
        assert await delete_batch(db, "provider_incidents", None, NOW, audit_days=0) == 0
        assert await delete_batch(db, "trace_cascades", None, NOW, audit_days=0) is None
        assert await delete_batch(db, "scores", uuid.uuid4(), NOW, audit_days=0) is None
    assert {"provider_incidents", "audit_events"} == GLOBAL_CLASSES


async def test_incomplete_means_a_batch_was_full_or_the_run_stopped_early(
    session_factory: SessionFactory,
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.INFO)
    await _expired_projects(session_factory, 1)
    monkeypatch.setattr(retention, "TRACE_BATCH", 1)
    await run_retention(await _context(session_factory, settings), {})
    [summary] = logged(caplog, "retention_applied")
    # One parent per batch: the first visit was full, so the run may have left work behind
    # (the second visit then took the other trace, but the flag reports what the run saw).
    assert summary["deleted"]["trace_cascades"] == 2
    assert summary["incomplete"] is True


async def test_a_held_cursor_row_is_skipped_and_all_held_rows_end_the_run_as_contended(
    session_factory: SessionFactory, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    await _reset_cursors(session_factory)
    await new_projects(session_factory, 1)
    context = await _context(session_factory, settings)
    async with session_factory() as holder:
        # Another worker holds the class that would be next: discovery skips it, no waiting.
        await holder.execute(
            text("SELECT class FROM retention_progress WHERE class = :c FOR UPDATE"),
            {"c": "annotation_queue_items"},
        )
        opportunity = await next_opportunity(context, _deadline())
        assert opportunity is not None
        assert opportunity.cleanup_class != "annotation_queue_items"
        await holder.rollback()
    async with session_factory() as holder:
        await holder.execute(text("SELECT class FROM retention_progress FOR UPDATE"))
        assert await next_opportunity(context, _deadline()) is None
        await run_retention(context, {})
        await holder.rollback()
    [summary] = logged(caplog, "retention_applied")
    assert summary["opportunities"] == 0
    assert summary["outcomes"] == {"contended": 1}
    assert summary["incomplete"] is True


async def test_a_database_error_in_discovery_ends_the_run_cleanly(
    session_factory: SessionFactory,
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.INFO)
    await _reset_cursors(session_factory)
    context = await _context(session_factory, settings)
    before = await _cursors(session_factory)
    beat = retention.progress.heartbeat
    heartbeats = 0
    async with session_factory() as holder:

        async def heartbeat_then_hold_job(context: TaskContext, deadline: float) -> None:
            nonlocal heartbeats
            await beat(context, deadline)
            heartbeats += 1
            # Held only now, so discovery rotates a cursor and then its fence hits the 250 ms
            # lock timeout; the heartbeat itself must not be the statement that fails.
            await holder.execute(
                text("SELECT id FROM jobs WHERE id = :id FOR UPDATE"), {"id": context.job.id}
            )

        monkeypatch.setattr(retention.progress, "heartbeat", heartbeat_then_hold_job)
        await run_retention(context, {})
        await holder.rollback()
    assert heartbeats == 1
    [summary] = logged(caplog, "retention_applied")
    assert summary["outcomes"] == {"discovery_error": 1}
    assert summary["opportunities"] == 0
    assert summary["incomplete"] is True
    assert await _cursors(session_factory) == before  # the rotation rolled back

"""`run_detectors` lists the active projects with the bypass and enqueues one job per project;
each project job runs bound to its project alone. Insights and run records must land in the
project whose spans produced them, and one project's failure must not touch another's run.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from prometheus_client import REGISTRY
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.insights import jobs
from app.insights.detectors.base import Detector
from app.insights.engine import DetectorRunResult
from app.insights.job_queries import active_project_ids, open_insights_by_severity
from app.insights.jobs import (
    ACTIVE_WINDOW,
    PROJECT_JOB,
    run_detectors,
    run_detectors_project,
    schedule_projects,
)
from app.insights.schemas import Severity
from app.jobs import queue
from app.jobs.context import TaskContext
from app.jobs.worker import Worker
from tests.jobs.bypass_support import (
    SessionFactory,
    execute,
    fetch,
    new_projects,
    seed_llm_spans,
    seed_row,
    visible_to,
)

# `unpriced_spend` opens an info insight for 20 or more unpriced calls of one model.
_ENOUGH = 20
_INSIGHTS = "SELECT project_id, kind, status, evidence FROM insights"
_PROJECT_JOBS = (
    "SELECT payload ->> 'project_id' AS project_id, status, dedupe_key, max_attempts, last_error "
    f"FROM jobs WHERE kind = '{PROJECT_JOB}' ORDER BY id"
)


async def _schedule(session_factory: SessionFactory, settings: Settings) -> None:
    """One `run_detectors` pass: lists the active projects and enqueues their jobs."""
    # The parent reads nothing from its own job row.
    job = queue.ClaimedJob(
        id=0, kind="run_detectors", payload={}, attempts=1, max_attempts=1, fence=1
    )
    context = TaskContext(job=job, settings=settings, session_factory=session_factory)
    await run_detectors(context, {})


async def _drain(session_factory: SessionFactory, settings: Settings) -> int:
    """Run every queued project job the way the worker does; returns how many ran. The pass
    spreads the jobs' due times out; the drain does not wait for them."""
    await execute(
        session_factory, f"UPDATE jobs SET run_after = now() WHERE kind = '{PROJECT_JOB}'"
    )
    worker = Worker(session_factory, settings, handlers={PROJECT_JOB: run_detectors_project})
    ran = 0
    while await worker.run_once():
        ran += 1
    return ran


async def test_active_projects_are_those_with_recent_spans(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC)
    a, b, idle, stale = await new_projects(session_factory, 4)
    await seed_llm_spans(session_factory, a, 1, started_at=now - timedelta(hours=1))
    await seed_llm_spans(session_factory, b, 1, started_at=now - timedelta(hours=23))
    await seed_llm_spans(session_factory, stale, 1, started_at=now - timedelta(hours=30))

    async with session_factory() as db:
        listed = await active_project_ids(db, now - ACTIVE_WINDOW, now)

    assert set(listed) == {a, b}  # neither the project without spans nor the one with old spans
    assert idle not in listed and stale not in listed


async def test_a_pass_enqueues_one_single_attempt_job_per_active_project_once_per_period(
    session_factory: SessionFactory, settings: Settings
) -> None:
    now = datetime.now(UTC)
    a, b, idle = await new_projects(session_factory, 3)
    await seed_llm_spans(session_factory, a, 1, started_at=now - timedelta(hours=1))
    await seed_llm_spans(session_factory, b, 1, started_at=now - timedelta(hours=1))

    await _schedule(session_factory, settings)
    await _schedule(session_factory, settings)  # the same period: a no-op

    rows = await fetch(session_factory, _PROJECT_JOBS)
    assert {uuid.UUID(row["project_id"]) for row in rows} == {a, b}
    assert len(rows) == 2 and {row["status"] for row in rows} == {"queued"}
    assert {row["max_attempts"] for row in rows} == {1}  # the next pass is the retry
    assert all(row["dedupe_key"].startswith("run_detectors:") for row in rows)
    assert idle not in {uuid.UUID(row["project_id"]) for row in rows}


async def test_insights_open_in_the_project_whose_spans_caused_them(
    session_factory: SessionFactory, settings: Settings
) -> None:
    now = datetime.now(UTC)
    a, b, below = await new_projects(session_factory, 3)
    started = now - timedelta(hours=1)
    await seed_llm_spans(session_factory, a, _ENOUGH, started_at=started, model="a-model")
    await seed_llm_spans(session_factory, b, _ENOUGH + 5, started_at=started, model="b-model")
    await seed_llm_spans(session_factory, below, _ENOUGH - 1, started_at=started, model="c-model")

    await _schedule(session_factory, settings)
    assert await _drain(session_factory, settings) == 3

    insights = await fetch(session_factory, _INSIGHTS)
    assert {(row["project_id"], row["kind"], row["status"]) for row in insights} == {
        (a, "unpriced_spend", "open"),
        (b, "unpriced_spend", "open"),
    }
    calls = {row["project_id"]: row["evidence"]["metrics"]["calls"] for row in insights}
    assert calls == {a: _ENOUGH, b: _ENOUGH + 5}
    runs = await fetch(
        session_factory, "SELECT project_id, count(*) AS runs FROM detector_runs GROUP BY 1"
    )
    assert {row["project_id"] for row in runs} == {a, b, below}
    assert len({row["runs"] for row in runs}) == 1  # every project ran the same detectors
    # What the job opened is invisible from the other project.
    seen_by_a = await visible_to(session_factory, a, _INSIGHTS)
    assert [row["project_id"] for row in seen_by_a] == [a]
    seen_by_b = await visible_to(session_factory, b, _INSIGHTS)
    assert [row["project_id"] for row in seen_by_b] == [b]
    # The project below the threshold has run records of its own and no insight.
    own_runs = await visible_to(session_factory, below, "SELECT project_id FROM detector_runs")
    assert own_runs and {row["project_id"] for row in own_runs} == {below}
    assert await visible_to(session_factory, below, _INSIGHTS) == []
    assert {row["status"] for row in await fetch(session_factory, _PROJECT_JOBS)} == {"done"}


async def test_a_pass_leaves_the_insights_of_projects_it_did_not_run(
    session_factory: SessionFactory, settings: Settings
) -> None:
    now = datetime.now(UTC)
    active, dormant = await new_projects(session_factory, 2)
    await seed_llm_spans(session_factory, active, _ENOUGH, started_at=now - timedelta(hours=1))
    # The dormant project has an open insight and no spans: no pass runs it, so it must stay open.
    seeded = await seed_row(session_factory, "insights", dormant)

    await _schedule(session_factory, settings)
    assert await _drain(session_factory, settings) == 1

    rows = await fetch(session_factory, "SELECT id, project_id, status, occurrences FROM insights")
    by_project = {row["project_id"]: row for row in rows}
    assert set(by_project) == {active, dormant}
    assert (by_project[dormant]["id"], by_project[dormant]["status"]) == (seeded["id"], "open")
    assert by_project[dormant]["occurrences"] == seeded["occurrences"]
    async with session_factory() as db:
        counts = await open_insights_by_severity(db)
    assert counts[Severity.INFO] == 2  # the gauge counts every project's open insights


async def test_one_failing_project_does_not_stop_the_others(
    session_factory: SessionFactory, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    broken, healthy = await new_projects(session_factory, 2)
    started = now - timedelta(hours=1)
    await seed_llm_spans(session_factory, broken, _ENOUGH, started_at=started, model="a-model")
    await seed_llm_spans(session_factory, healthy, _ENOUGH, started_at=started, model="b-model")
    real_run_project = jobs.run_project

    async def run_project(
        db: AsyncSession,
        project_id: uuid.UUID,
        now: datetime,
        detectors: tuple[Detector, ...],
        **kwargs: Any,
    ) -> list[DetectorRunResult]:
        if project_id == broken:
            raise RuntimeError("context read failed")
        return await real_run_project(db, project_id, now, detectors, **kwargs)

    monkeypatch.setattr(jobs, "run_project", run_project)

    await _schedule(session_factory, settings)
    assert await _drain(session_factory, settings) == 2

    insights = await fetch(session_factory, _INSIGHTS)
    assert {(row["project_id"], row["kind"]) for row in insights} == {(healthy, "unpriced_spend")}
    errors = await fetch(
        session_factory,
        "SELECT project_id, count(*) AS rows, min(error) AS error FROM detector_runs "
        "WHERE error IS NOT NULL GROUP BY 1",
    )
    assert [(row["project_id"], row["error"]) for row in errors] == [
        (broken, "RuntimeError: context read failed")
    ]
    assert errors[0]["rows"] == len(jobs.DETECTORS)  # one error row per detector
    # Both jobs ran to the end: the failure is recorded in detector health, not on the job.
    assert {row["status"] for row in await fetch(session_factory, _PROJECT_JOBS)} == {"done"}


async def test_project_jobs_are_staggered_so_other_short_jobs_interleave(
    session_factory: SessionFactory, settings: Settings
) -> None:
    """A notification job due now is claimed ahead of the project jobs of a pass, not behind
    all of them: only the first project job is due at once, the rest are spread out."""
    now = datetime.now(UTC)
    projects = await new_projects(session_factory, 20)
    for project_id in projects:
        await seed_llm_spans(session_factory, project_id, 1, started_at=now - timedelta(hours=1))

    await _schedule(session_factory, settings)
    async with session_factory() as db:
        await queue.enqueue(db, "deliver_notifications")
        await db.commit()

    claimed: list[str] = []
    async with session_factory() as db:
        while job := await queue.claim_next(
            db, supported_kinds={PROJECT_JOB, "deliver_notifications"}
        ):
            claimed.append(job.kind)
            await db.commit()
    assert "deliver_notifications" in claimed[:2]
    assert claimed.count(PROJECT_JOB) < len(projects)  # the later project jobs are not due yet


async def test_a_project_job_from_a_stale_pass_is_skipped_without_detector_work(
    session_factory: SessionFactory, settings: Settings
) -> None:
    """A backlog never grows across passes: a project job whose pass is older than one period
    does nothing (the next pass covers its project with a fresh `now`); a fresh one runs."""
    now = datetime.now(UTC)
    stale, fresh = await new_projects(session_factory, 2)
    for project_id in (stale, fresh):
        await seed_llm_spans(session_factory, project_id, 1, started_at=now - timedelta(hours=1))
    skipped_before = _stale_skips()
    await schedule_projects(session_factory, [stale], now - timedelta(minutes=16))
    await schedule_projects(session_factory, [fresh], now)

    assert await _drain(session_factory, settings) == 2

    runs = await fetch(session_factory, "SELECT DISTINCT project_id FROM detector_runs")
    assert [row["project_id"] for row in runs] == [fresh]
    assert _stale_skips() == skipped_before + 1
    # Skipping is not a failure: the job is done, and the next pass lists the project again.
    rows = await fetch(session_factory, _PROJECT_JOBS)
    assert [(row["status"], row["last_error"]) for row in rows] == [("done", None)] * 2


def _stale_skips() -> float:
    return REGISTRY.get_sample_value("spanlight_detector_projects_stale_total") or 0.0

"""Evaluator runs written and read straight in the database, for the reservation tests.

`judge_evaluator` makes an organization, a project and an `llm_judge` evaluator row (its config
is never parsed by the reservation code). `insert_run` adds a run in any state. `reserve` runs
`reserve_run` in a transaction of its own bound to the project, as the runner does. `run_row`
and `spend` read back past row-level security, as the worker does. `reserved_run`,
`reaper_world` and `delete_evaluator` set up the settle and reaper cases.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bind_project, bypass_rls
from app.evals.reservation import MonthSpend, month_spend, reserve_run
from tests.rls.factories import create_project

Sessions = async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class ReaperRuns:
    """Runs of one judge around the reaper's ten minutes. `stale` and `alive` are running, holding
    0.3, with heartbeats 11 and 9 minutes old; `orphaned` and `waiting` are queued online runs
    queued 11 minutes ago, whose `run_evaluator` job died (`orphaned`) or is still queued."""

    stale: uuid.UUID
    alive: uuid.UUID
    orphaned: uuid.UUID
    waiting: uuid.UUID


async def judge_evaluator(sessions: Sessions, budget: str = "1.0") -> tuple[uuid.UUID, uuid.UUID]:
    """A fresh project with one `llm_judge` evaluator; returns (project_id, evaluator_id)."""
    evaluator_id = uuid.uuid4()
    async with sessions() as db:
        await bypass_rls(db)
        project_id = await create_project(db)
        await db.execute(
            text(
                "INSERT INTO evaluators (id, project_id, name, kind, config, monthly_budget_usd) "
                "VALUES (:id, :project_id, 'judge', 'llm_judge', '{\"kind\": \"llm_judge\"}', "
                ":budget)"
            ),
            {"id": evaluator_id, "project_id": project_id, "budget": Decimal(budget)},
        )
        await db.commit()
    return project_id, evaluator_id


async def insert_run(
    sessions: Sessions,
    evaluator_id: uuid.UUID,
    *,
    started_at: datetime,
    status: str = "queued",
    source: str = "test",
    heartbeat_at: datetime | None = None,
    reserved: str = "0",
) -> uuid.UUID:
    """One run of the evaluator on a fresh trace id."""
    run_id = uuid.uuid4()
    async with sessions() as db:
        await bypass_rls(db)
        await db.execute(
            text(
                "INSERT INTO evaluator_runs (id, evaluator_id, trace_id, status, source, "
                "started_at, heartbeat_at, reserved_usd) "
                "VALUES (:id, :evaluator_id, :trace_id, :status, :source, :started_at, "
                ":heartbeat_at, :reserved)"
            ),
            {
                "id": run_id,
                "evaluator_id": evaluator_id,
                "trace_id": uuid.uuid4().hex,
                "status": status,
                "source": source,
                "started_at": started_at,
                "heartbeat_at": heartbeat_at,
                "reserved": Decimal(reserved),
            },
        )
        await db.commit()
    return run_id


async def reserve(
    sessions: Sessions,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    *,
    estimate: Decimal,
    budget: Decimal,
    now: datetime,
) -> bool:
    """`reserve_run` in one committed transaction bound to the project."""
    async with sessions() as db, db.begin():
        await bind_project(db, project_id)
        return await reserve_run(db, run_id, estimate, budget, now)


async def run_row(sessions: Sessions, run_id: uuid.UUID) -> dict[str, Any]:
    """The run's row."""
    async with sessions() as db:
        await bypass_rls(db)
        row = await db.execute(text("SELECT * FROM evaluator_runs WHERE id = :id"), {"id": run_id})
        return dict(row.mappings().one())


async def spend(sessions: Sessions, evaluator_id: uuid.UUID, now: datetime) -> MonthSpend:
    """The evaluator's month so far, as the next reservation reads it."""
    async with sessions() as db:
        await bypass_rls(db)
        return await month_spend(db, evaluator_id, now)


async def enqueue_run_job(sessions: Sessions, run_id: uuid.UUID) -> None:
    """The run's `run_evaluator` job, still queued, as the sampler leaves it."""
    async with sessions() as db:
        await db.execute(
            text(
                "INSERT INTO jobs (kind, dedupe_key, status) "
                "VALUES ('run_evaluator', :key, 'queued')"
            ),
            {"key": f"run_evaluator:{run_id}"},
        )
        await db.commit()


async def delete_evaluator(sessions: Sessions, evaluator_id: uuid.UUID) -> None:
    """Delete the evaluator, and with it every run of it (the foreign key cascades)."""
    async with sessions() as db:
        await bypass_rls(db)
        await db.execute(text("DELETE FROM evaluators WHERE id = :id"), {"id": evaluator_id})
        await db.commit()


async def reserved_run(
    sessions: Sessions, evaluator_id: uuid.UUID, project_id: uuid.UUID, at: datetime
) -> uuid.UUID:
    """A run admitted at `at`, holding a reservation of 0.3 under a budget of 1.0."""
    run_id = await insert_run(sessions, evaluator_id, started_at=at)
    admitted = await reserve(
        sessions, project_id, run_id, estimate=Decimal("0.3"), budget=Decimal("1.0"), now=at
    )
    assert admitted
    return run_id


async def reaper_world(sessions: Sessions, evaluator_id: uuid.UUID, now: datetime) -> ReaperRuns:
    """The runs the reaper must tell apart at `now` (see `ReaperRuns`)."""
    old, recent = now - timedelta(minutes=11), now - timedelta(minutes=9)

    async def running(heartbeat: datetime) -> uuid.UUID:
        return await insert_run(
            sessions,
            evaluator_id,
            started_at=heartbeat,
            status="running",
            heartbeat_at=heartbeat,
            reserved="0.3",
        )

    waiting = await insert_run(sessions, evaluator_id, started_at=old, source="online")
    await enqueue_run_job(sessions, waiting)
    return ReaperRuns(
        stale=await running(old),
        alive=await running(recent),
        orphaned=await insert_run(sessions, evaluator_id, started_at=old, source="online"),
        waiting=waiting,
    )

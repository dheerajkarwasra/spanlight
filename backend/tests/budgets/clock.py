"""Controlled time for the budget tests and for `/health/ready`.

The budget tests run the app on a `ManualClock` pinned to yesterday 12:00 UTC (`pinned_start`):
the gateway's guard, its span times and the budgets API all read it, and ingested spend is
stamped from it, so a daily period never ends mid-test whatever the wall clock says.
`next_pass` moves it to the next minute, where the worker's per-minute schedule would run a
pass. `write_heartbeat` and `write_evaluation_job` put a worker heartbeat and a finished
`evaluate_alerts` job at a chosen age on the database clock, the clock readiness measures with.
`ready_with` probes readiness on an app with other settings.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.budgets.periods import period_start
from app.budgets.types import BudgetPeriod
from app.config import Settings
from app.jobs.scheduler import EVALUATE_ALERTS, period_key
from app.main import create_app
from tests.gateway_support import ManualClock

Sessions = async_sessionmaker[AsyncSession]
TEST_ORIGIN = "http://testserver"
MINUTE = timedelta(minutes=1)


def pinned_start() -> datetime:
    """Yesterday 12:00 UTC: twelve hours from either end of its day, in the past."""
    today = period_start(BudgetPeriod.DAILY, datetime.now(UTC))
    return today - timedelta(hours=12)


def next_pass(clock: ManualClock) -> datetime:
    """Move `clock` to the start of its next minute and return it: the `now` of a pass there.

    A pass reads windows that end on the minute, so it sees everything stamped before it. Each
    call is a later minute, so successive passes never repeat (a pass at the stored time counts
    as an older one).
    """
    now = clock.wall()
    upcoming = now.replace(second=0, microsecond=0) + MINUTE
    clock.advance((upcoming - now).total_seconds())
    return upcoming


async def write_heartbeat(sessions: Sessions, age: timedelta) -> None:
    """A worker whose last beat was `age` ago."""
    async with sessions() as db:
        await db.execute(
            text(
                "INSERT INTO worker_heartbeats (worker_id, last_seen_at, version) "
                "VALUES ('stopped-worker', now() - make_interval(secs => :age), 'test')"
            ),
            {"age": age.total_seconds()},
        )
        await db.commit()


async def write_evaluation_job(sessions: Sessions, age: timedelta, status: str = "done") -> None:
    """An `evaluate_alerts` job queued `age` ago, with the scheduler's dedupe key for then."""
    queued = datetime.now(UTC) - age
    async with sessions() as db:
        await db.execute(
            text(
                "INSERT INTO jobs (kind, status, dedupe_key, max_attempts, created_at) "
                "VALUES (:kind, CAST(:status AS job_status), :key, 1, "
                "now() - make_interval(secs => :age))"
            ),
            {
                "kind": EVALUATE_ALERTS.kind,
                "status": status,
                "key": period_key(EVALUATE_ALERTS, queued),
                "age": age.total_seconds(),
            },
        )
        await db.commit()


@asynccontextmanager
async def _running(settings: Settings) -> AsyncIterator[Any]:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


async def ready_with(settings: Settings, **overrides: Any) -> httpx.Response:
    """`GET /health/ready` on an app started with `settings` changed by `overrides`."""
    async with _running(settings.model_copy(update=overrides)) as application:
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url=TEST_ORIGIN) as http:
            return await http.get("/health/ready")

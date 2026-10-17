"""Writing the process's loss counts to `telemetry_losses_hourly`: every minute, and at shutdown.

`flush_once` drains the `LossCounter`, groups the counts by project and writes each project's in
one short transaction bound to that project (`bind_project`). It never sets the row-level
security bypass: the api runs it, and no api module may (`tests/rls/test_bypass_callers.py`).

A project whose write fails gets its counts back for the next flush, whatever the failure: the
database down, a timeout, a failover, even a bug (then it fails, loudly, every minute; the
counter's `MAX_KEYS` bounds what stays stuck). The one exception is a project that no longer
exists (SQLSTATE 23503, the foreign key): its counts have nowhere to go and are dropped. Every
failure is logged as `telemetry_losses.flush_failed` by the error's type and SQLSTATE, never its
message, with `kept` saying which way it went.

`start` runs the flush every `FLUSH_INTERVAL_S` on the running loop; `stop` runs the last one
and waits for it at most `timeout_s`, logging one `flush_failed` line (`stage="shutdown"`) when
it gives up. Counts not written by then stay in the counter: a process
that exits loses them, as it loses up to a minute of counts when it is killed. A flush cut short
puts back the counts of every project it had not finished, so a project whose commit went through
just as it was cut may be counted twice: an overcount keeps the completeness ratio a lower bound.

Each app's lifespan starts and stops the flush (`app.main`), and so does the worker
(`app.jobs.worker`), whose eval runs, judges and demo traffic write gateway spans too. In a
process running two apps (some tests), the flush keeps running until the last of them stops it.
"""

import asyncio
import contextlib
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db_errors import sqlstate
from app.db.rls import bind_project
from app.gateway.span_losses import loss_counter
from app.telemetry_losses.counter import LossCounter, LossKey
from app.telemetry_losses.service import upsert_losses

logger = structlog.get_logger(__name__)

FLUSH_INTERVAL_S = 60.0
SHUTDOWN_BUDGET_S = 5.0
# `foreign_key_violation`: the project was deleted, so its counts can never be written.
PROJECT_GONE = "23503"

type _ProjectEntries = dict[tuple[str, str, datetime], int]


@dataclass
class _Flusher:
    loop: asyncio.AbstractEventLoop
    task: "asyncio.Task[None]"
    stopping: asyncio.Event
    users: int = 1  # the lifespans that started it and have not stopped it


_flusher: _Flusher | None = None


async def flush_once(sessions: async_sessionmaker[AsyncSession], counter: LossCounter) -> int:
    """Write every count in `counter`, project by project. Returns the lost spans written.

    Never raises but for cancellation, after which the unwritten counts are back in `counter`.
    """
    by_project = _by_project(counter.drain())
    written = 0
    pending = list(by_project)
    try:
        while pending:
            project_id = pending[0]
            entries = by_project[project_id]
            if await _write_project(sessions, counter, project_id, entries):
                written += sum(entries.values())
            pending.pop(0)
    except asyncio.CancelledError:
        counter.restore(_keys(by_project, pending))
        raise
    if written:
        logger.info("telemetry_losses.flushed", projects=len(by_project), losses=written)
    return written


def start(sessions: async_sessionmaker[AsyncSession], counter: LossCounter | None = None) -> None:
    """Flush `counter` (the gateway's by default) every `FLUSH_INTERVAL_S` on the running loop."""
    global _flusher  # noqa: PLW0603 - one flush per process, rebuilt for a new loop
    loop = asyncio.get_running_loop()
    if _flusher is not None and _flusher.loop is loop and not _flusher.task.done():
        _flusher.users += 1
        return
    stopping = asyncio.Event()
    task = loop.create_task(_run(sessions, counter or loss_counter(), stopping))
    _flusher = _Flusher(loop=loop, task=task, stopping=stopping)


async def stop(*, timeout_s: float) -> None:
    """Run the last flush and wait for it at most `timeout_s`, then stop the flush."""
    global _flusher  # noqa: PLW0603 - one flush per process
    flusher = _flusher
    if flusher is None or flusher.loop is not asyncio.get_running_loop():
        return
    flusher.users -= 1
    if flusher.users > 0:
        return
    _flusher = None
    flusher.stopping.set()
    try:
        await asyncio.wait_for(flusher.task, timeout_s)
    except TimeoutError:
        # The counts it had not written are back in the counter (`flush_once`).
        logger.warning(
            "telemetry_losses.flush_failed",
            stage="shutdown",
            error_type="TimeoutError",
            kept=True,
            timeout_s=timeout_s,
        )


async def _run(
    sessions: async_sessionmaker[AsyncSession], counter: LossCounter, stopping: asyncio.Event
) -> None:
    """Flush every `FLUSH_INTERVAL_S` and once more when `stopping` is set, then return."""
    while True:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stopping.wait(), FLUSH_INTERVAL_S)
        last = stopping.is_set()  # set during this flush: one more, for what came in meanwhile
        await flush_once(sessions, counter)
        if last:
            return


async def _write_project(
    sessions: async_sessionmaker[AsyncSession],
    counter: LossCounter,
    project_id: uuid.UUID,
    entries: _ProjectEntries,
) -> bool:
    """One project's counts in one transaction bound to it. False when they were not written."""
    try:
        async with sessions() as db:
            await bind_project(db, project_id)
            await upsert_losses(db, project_id, entries)
            await db.commit()
    except Exception as failure:  # noqa: BLE001 - logged; the counts are kept or dropped
        kept = sqlstate(failure) != PROJECT_GONE
        if kept:
            counter.restore(_keys({project_id: entries}, [project_id]))
        logger.warning(
            "telemetry_losses.flush_failed",
            stage="flush",
            project_id=str(project_id),
            error_type=type(failure).__name__,
            sqlstate=sqlstate(failure),
            kept=kept,
            losses=sum(entries.values()),
        )
        return False
    return True


def _by_project(drained: dict[LossKey, int]) -> dict[uuid.UUID, _ProjectEntries]:
    """The drained counts by project, in project order (so the flush order is stable)."""
    grouped: defaultdict[uuid.UUID, _ProjectEntries] = defaultdict(dict)
    for (project_id, source, reason, hour), n in drained.items():
        grouped[project_id][(source, reason, hour)] = n
    return dict(sorted(grouped.items(), key=lambda item: str(item[0])))


def _keys(
    by_project: dict[uuid.UUID, _ProjectEntries], projects: list[uuid.UUID]
) -> dict[LossKey, int]:
    """The counts of `projects`, keyed as the counter keys them."""
    return {
        (project_id, source, reason, hour): n
        for project_id in projects
        for (source, reason, hour), n in by_project[project_id].items()
    }

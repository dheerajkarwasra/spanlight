"""The run ledger of a billing source: open a run, write its lines, finish it (spec §6.6, §10.2).

A fetch opens its run in one transaction, then writes the lines, finishes the run and updates
the source in a second one, so a failed fetch writes nothing but the failed run itself. The
manual refresh and the CSV upload use the same functions. Callers commit.

`open_run` takes the source row `FOR NO KEY UPDATE` before it looks for a run in progress, so of
two callers racing to open a run of one source only the first succeeds: the second waits for the
first to commit, then sees its run. That lock does not block the foreign-key checks of other
writers (they take `FOR KEY SHARE`).

A run's `error` and a source's `last_error` hold an HTTP status line or one of the fixed phrases
of the fetchers, never a provider's response body. Every query names the source's `org_id`.
"""

import uuid
from collections.abc import Sequence
from datetime import date, timedelta
from itertools import batched
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.db.models.billing import (
    BillingFetchRun,
    BillingSource,
    FetchTrigger,
    ProviderCostLine,
    SourceStatus,
)
from app.reconciliation.types import MappedLine

# The job kind of one source's fetch; the scheduled parent and the manual refresh enqueue it.
FETCH_SOURCE_JOB = "fetch_billing_source"
# A run still `running` after this long is taken for dead (its worker crashed or was killed).
RUN_IN_PROGRESS_WINDOW = timedelta(minutes=15)
# What a run taken for dead is closed with: one of the fixed phrases.
STALE_RUN_ERROR = "timeout"
# What a manual run is closed with when its source was disabled before its job ran. (A deleted
# source takes its runs with it.)
SOURCE_DISABLED_ERROR = "source disabled"
# Rows per upsert statement: 19 columns each stays far below Postgres' 65,535 parameters.
_UPSERT_BATCH = 1000


class RunInProgress(Exception):  # noqa: N818 - reads as the outcome, like the crypto errors
    """Another run of the source is `running` and younger than `RUN_IN_PROGRESS_WINDOW`."""


class SourceGone(Exception):  # noqa: N818
    """The source (and with it the run) was deleted while the run was in flight."""


class RunClosed(Exception):  # noqa: N818
    """The run is no longer `running`: another fetch closed it as dead (`STALE_RUN_ERROR`)."""


def child_payload(
    source_id: uuid.UUID,
    day_from: date,
    day_to: date,
    *,
    trigger: FetchTrigger | str,
    requested_by: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """The payload of a `FETCH_SOURCE_JOB` job. `run_id` names a run the caller already opened
    (the manual refresh), which the job then carries on instead of opening its own."""
    body: dict[str, Any] = {
        "source_id": str(source_id),
        "day_from": day_from.isoformat(),
        "day_to": day_to.isoformat(),
        "trigger": trigger,
    }
    if requested_by is not None:
        body["requested_by"] = str(requested_by)
    if run_id is not None:
        body["run_id"] = str(run_id)
    return body


async def open_run(
    db: AsyncSession,
    source: BillingSource,
    *,
    trigger: FetchTrigger,
    day_from: date,
    day_to: date,
    requested_by: uuid.UUID | None,
) -> BillingFetchRun:
    """A new `running` run of the source over `[day_from, day_to]`.

    Raises `RunInProgress` when another run of the source is `running` and younger than 15
    minutes, and `SourceGone` when the source no longer exists. Older `running` runs are closed
    as failed (`timeout`) first: their worker is gone.
    """
    locked = await db.scalar(
        select(BillingSource.id)
        .where(BillingSource.id == source.id, BillingSource.org_id == source.org_id)
        .with_for_update(key_share=True)
    )
    if locked is None:
        raise SourceGone
    await _refuse_or_close_running(db, source)
    inserted = await db.execute(
        insert(BillingFetchRun)
        .values(
            id=new_id(),
            source_id=source.id,
            org_id=source.org_id,
            trigger=trigger,
            day_from=day_from,
            day_to=day_to,
            status="running",
            requested_by=requested_by,
        )
        .returning(BillingFetchRun)
    )
    run: BillingFetchRun = inserted.scalar_one()
    return run


async def _refuse_or_close_running(db: AsyncSession, source: BillingSource) -> None:
    """`RunInProgress` while a run of the source is younger than the window; older `running`
    runs are closed as failed with `STALE_RUN_ERROR`."""
    running = (*_of_source(source), BillingFetchRun.status == "running")
    busy = await db.scalar(
        select(func.count())
        .select_from(BillingFetchRun)
        .where(*running, BillingFetchRun.started_at > func.now() - RUN_IN_PROGRESS_WINDOW)
    )
    if busy:
        raise RunInProgress
    await db.execute(
        update(BillingFetchRun)
        .where(*running)
        .values(status="failed", error=STALE_RUN_ERROR, finished_at=func.now())
    )


async def adopt_run(
    db: AsyncSession, source: BillingSource, run_id: uuid.UUID
) -> BillingFetchRun | None:
    """The source's run `run_id` while it is still `running`, its `started_at` reset to now;
    None otherwise. A manual run may wait in the queue for minutes: restarting its clock keeps a
    concurrent `open_run` from closing it as dead while this attempt works under it. Takes the
    source lock `open_run` takes, so the two cannot interleave."""
    locked = await db.scalar(
        select(BillingSource.id)
        .where(BillingSource.id == source.id, BillingSource.org_id == source.org_id)
        .with_for_update(key_share=True)
    )
    if locked is None:
        return None
    adopted = await db.execute(
        update(BillingFetchRun)
        .where(
            *_of_source(source),
            BillingFetchRun.id == run_id,
            BillingFetchRun.status == "running",
        )
        .values(started_at=func.now())
        .returning(BillingFetchRun)
    )
    found: BillingFetchRun | None = adopted.scalar_one_or_none()
    return found


async def write_lines(db: AsyncSession, run: BillingFetchRun, lines: Sequence[MappedLine]) -> int:
    """Upsert the lines on `(source_id, day, line_key)` as written by `run`; returns how many.

    A new row gets `first_seen_at`; an existing one keeps it and takes the new `amount_usd`,
    `quantity`, `model_pattern`, `fetch_run_id` and `last_seen_at`, so a provider correction
    replaces the old figure on the same row. Two lines with one day and key are refused
    (`ValueError`) before anything is written: one statement cannot update a row twice.
    """
    if len({(line.day, line.line_key) for line in lines}) != len(lines):
        raise ValueError("two lines share a day and a line key")
    for batch in batched(lines, _UPSERT_BATCH):
        statement = insert(ProviderCostLine).values([_line_row(run, line) for line in batch])
        statement = statement.on_conflict_do_update(
            index_elements=[
                ProviderCostLine.source_id,
                ProviderCostLine.day,
                ProviderCostLine.line_key,
            ],
            set_={
                "amount_usd": statement.excluded.amount_usd,
                "quantity": statement.excluded.quantity,
                "model_pattern": statement.excluded.model_pattern,
                "fetch_run_id": statement.excluded.fetch_run_id,
                "last_seen_at": statement.excluded.last_seen_at,
            },
        )
        await db.execute(statement)
    return len(lines)


async def finish_run_ok(
    db: AsyncSession, run: BillingFetchRun, *, lines_upserted: int, pages: int
) -> None:
    """Mark the run `ok` with its counts and the source `ok`, fetched and succeeded now. Only a
    `running` run changes. Raises `SourceGone` when the run no longer exists (its source was
    deleted), `RunClosed` when another fetch closed it as dead; the caller rolls back its lines."""
    result = await db.execute(
        update(BillingFetchRun)
        .where(
            BillingFetchRun.id == run.id,
            BillingFetchRun.org_id == run.org_id,
            BillingFetchRun.status == "running",
        )
        .values(
            status="ok",
            error=None,
            lines_upserted=lines_upserted,
            pages=pages,
            finished_at=func.now(),
        )
    )
    if getattr(result, "rowcount", 0) != 1:
        exists = await db.scalar(
            select(BillingFetchRun.id).where(
                BillingFetchRun.id == run.id, BillingFetchRun.org_id == run.org_id
            )
        )
        raise SourceGone if exists is None else RunClosed
    await db.execute(
        _source_update(run).values(
            status="ok", last_error=None, last_fetched_at=func.now(), last_success_at=func.now()
        )
    )


async def finish_run_failed(
    db: AsyncSession,
    run: BillingFetchRun,
    *,
    error: str | None,
    source_status: SourceStatus | None,
) -> None:
    """Mark the run `failed` with `error` (a status line; None when the cause is unknown).

    With a `source_status`, the source takes it, `error` as `last_error`, and the fetch time;
    None leaves the source as it is (an attempt that will be retried). Only a `running` run is
    changed, and the source only along with it: a run that already ended (`ok` with its lines
    committed) or no longer exists is left alone.
    """
    result = await db.execute(
        update(BillingFetchRun)
        .where(
            BillingFetchRun.id == run.id,
            BillingFetchRun.org_id == run.org_id,
            BillingFetchRun.status == "running",
        )
        .values(status="failed", error=error, finished_at=func.now())
    )
    if source_status is not None and getattr(result, "rowcount", 0) == 1:
        await db.execute(
            _source_update(run).values(
                status=source_status, last_error=error, last_fetched_at=func.now()
            )
        )


def _of_source(source: BillingSource) -> tuple[Any, ...]:
    """The conditions selecting one source's runs."""
    return (BillingFetchRun.source_id == source.id, BillingFetchRun.org_id == source.org_id)


def _source_update(run: BillingFetchRun) -> Any:
    return update(BillingSource).where(
        BillingSource.id == run.source_id, BillingSource.org_id == run.org_id
    )


def _line_row(run: BillingFetchRun, line: MappedLine) -> dict[str, Any]:
    return {
        "id": new_id(),
        "source_id": run.source_id,
        "org_id": run.org_id,
        "fetch_run_id": run.id,
        "day": line.day,
        "provider": line.provider,
        "line_key": line.line_key,
        "line_kind": line.line_kind,
        "model_raw": line.model_raw,
        "model_pattern": line.model_pattern,
        "token_type": line.token_type,
        "service_tier": line.service_tier,
        "context_window": line.context_window,
        "provider_scope": line.provider_scope,
        "description": line.description,
        "quantity": line.quantity,
        "amount_usd": line.amount_usd,
        "first_seen_at": func.now(),
        "last_seen_at": func.now(),
    }

"""Retention of the provider's side of reconciliation: cost lines and the runs that fetched them.

An invoice outlives the traces it is compared with, so these rows are kept
`PROVIDER_LINES_RETENTION_DAYS` whatever a project's own retention is (spec §6.10). A line goes
once its UTC day ends before the cutoff; a run goes once it started before the cutoff and no line
points at it any more. Lines are deleted first because `provider_cost_lines.fetch_run_id` is
`ON DELETE RESTRICT`. Both deletes walk one source at a time, so each batch reads the
`(source_id, day)` and `(source_id, started_at)` indexes, and commit per batch of `BATCH_SIZE`.

Neither table is under row-level security (they are organization-scoped, like
`provider_credentials`), so the retention job runs these without the worker bypass.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import TextClause

from app.db.models import BillingSource

PROVIDER_LINES_RETENTION_DAYS = 400
BATCH_SIZE = 5000

_DELETE_OLD_PROVIDER_LINES = text(
    """
    DELETE FROM provider_cost_lines
    WHERE id IN (
        SELECT id
        FROM provider_cost_lines
        WHERE source_id = :source_id
          AND day < (CAST(:cutoff AS timestamptz) AT TIME ZONE 'UTC')::date
        LIMIT :batch_size
    )
    """
)
_DELETE_OLD_FETCH_RUNS = text(
    """
    DELETE FROM billing_fetch_runs
    WHERE id IN (
        SELECT r.id
        FROM billing_fetch_runs r
        WHERE r.source_id = :source_id
          AND r.started_at < :cutoff
          AND NOT EXISTS (SELECT 1 FROM provider_cost_lines l WHERE l.fetch_run_id = r.id)
        LIMIT :batch_size
    )
    """
)


async def apply_billing_retention(
    session_factory: async_sessionmaker[AsyncSession], *, now: datetime
) -> tuple[int, int]:
    """Delete provider cost lines, then fetch runs, older than `PROVIDER_LINES_RETENTION_DAYS`.

    Returns how many lines and how many runs went.
    """
    cutoff = now - timedelta(days=PROVIDER_LINES_RETENTION_DAYS)
    async with session_factory() as db:
        sources = (await db.scalars(select(BillingSource.id))).all()

    lines = runs = 0
    for source_id in sources:
        lines += await _delete_in_batches(
            session_factory, _DELETE_OLD_PROVIDER_LINES, source_id=source_id, cutoff=cutoff
        )
        runs += await _delete_in_batches(
            session_factory, _DELETE_OLD_FETCH_RUNS, source_id=source_id, cutoff=cutoff
        )
    return lines, runs


async def _delete_in_batches(
    session_factory: async_sessionmaker[AsyncSession],
    statement: TextClause,
    *,
    source_id: uuid.UUID,
    cutoff: datetime,
) -> int:
    """Run a batched DELETE until a batch comes back short, one transaction per batch."""
    total = 0
    while True:
        params = {"source_id": source_id, "cutoff": cutoff, "batch_size": BATCH_SIZE}
        async with session_factory() as db:
            result = await db.execute(statement, params)
            await db.commit()
        deleted = int(getattr(result, "rowcount", 0))
        total += deleted
        if deleted < BATCH_SIZE:
            return total

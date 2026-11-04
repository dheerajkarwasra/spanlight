"""Retention of the provider's side of reconciliation: cost lines and the runs that fetched them.

An invoice outlives the traces it is compared with, so these rows are kept
`PROVIDER_LINES_RETENTION_DAYS` whatever a project's own retention is (spec §6.10). A line goes
once its UTC day ends before the cutoff; a run goes once it started before the cutoff and no line
points at it any more (`provider_cost_lines.fetch_run_id` is `ON DELETE RESTRICT`, so a run keeps
waiting while a later line of its still exists, whichever class is served first). Each statement
deletes at most `:batch_size` rows of one billing source, reading the `(source_id, day)` and
`(source_id, started_at)` indexes; the retention job (`app.jobs.tasks.retention`) gives each
source one batch per opportunity and commits.

Neither table is under row-level security (they are organization-scoped, like
`provider_credentials`), so the retention job runs these without the worker bypass.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PROVIDER_LINES_RETENTION_DAYS = 400

DELETE_OLD_PROVIDER_LINES = text(
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
DELETE_OLD_FETCH_RUNS = text(
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
# The retention job's cleanup class for each statement.
BILLING_DELETES = {
    "provider_cost_lines": DELETE_OLD_PROVIDER_LINES,
    "fetch_runs": DELETE_OLD_FETCH_RUNS,
}


async def delete_billing_batch(
    db: AsyncSession, cleanup_class: str, source_id: uuid.UUID, now: datetime, batch_size: int
) -> int:
    """Delete up to `batch_size` of one source's lines or fetch runs older than the 400 days.
    Returns how many went. Does not commit."""
    result = await db.execute(
        BILLING_DELETES[cleanup_class],
        {
            "source_id": source_id,
            "cutoff": now - timedelta(days=PROVIDER_LINES_RETENTION_DAYS),
            "batch_size": batch_size,
        },
    )
    return int(getattr(result, "rowcount", 0))

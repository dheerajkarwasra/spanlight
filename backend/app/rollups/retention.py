"""Delete one bounded batch of a project's hourly rollups that are older than its retention.

Rollups are derived from the raw rows, so they must not outlive them: the span, trace and error
rollups go with the spans and traces they count. Telemetry losses count spans that never arrived
and follow the same retention. Only whole hours that end before the cutoff go; the hour that holds
the cutoff may still have spans behind it, so the caller passes the cutoff rounded down to its
hour.

The retention job (``app.jobs.tasks.retention``) calls this with the row-level security bypass
set and `lock_aggregate_sources` held on the session, and commits. Each of the four tables gives
up at most `batch_size` rows per call, so one call deletes at most four times `batch_size` rows;
what is left waits for the project's next opportunity.
"""

import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Every table here has a surrogate `id` and an index that starts with `(project_id, bucket_start)`.
_HOURLY_TABLES = (
    "span_rollups_hourly",
    "trace_rollups_hourly",
    "error_rollups_hourly",
    "telemetry_losses_hourly",
)
_DELETES = {
    table: text(
        f"DELETE FROM {table} WHERE project_id = :project_id AND id IN ("
        f"SELECT id FROM {table} WHERE project_id = :project_id AND bucket_start < :bucket_cutoff "
        "ORDER BY bucket_start, id LIMIT :batch_size)"
    )
    for table in _HOURLY_TABLES
}


async def delete_hourly_rollups(
    db: AsyncSession, project_id: uuid.UUID, bucket_cutoff: datetime, batch_size: int
) -> int:
    """Delete up to `batch_size` of the project's rows with ``bucket_start < bucket_cutoff`` from
    each hourly table, oldest first. Returns how many rows went in all. Does not commit."""
    params = {"project_id": project_id, "bucket_cutoff": bucket_cutoff, "batch_size": batch_size}
    deleted = 0
    for statement in _DELETES.values():
        result = await db.execute(statement, params)
        deleted += int(getattr(result, "rowcount", 0))
    return deleted

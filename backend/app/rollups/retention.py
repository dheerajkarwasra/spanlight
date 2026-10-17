"""Delete a project's hourly rollups that are older than its retention.

Rollups are derived from the raw rows, so they must not outlive them: the span, trace and error
rollups go with the spans and traces they count. Telemetry losses count spans that never arrived
and follow the same retention. Only whole hours that end before the cutoff go; the hour that holds
the cutoff may still have spans behind it, so the caller passes the cutoff rounded down to its
hour.

The retention job (``app.jobs.tasks.retention``) calls this with the row-level security bypass
already set on the session, and commits.
"""

import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Every table here has an index that starts with `(project_id, bucket_start)`.
_HOURLY_TABLES = (
    "span_rollups_hourly",
    "trace_rollups_hourly",
    "error_rollups_hourly",
    "telemetry_losses_hourly",
)
_DELETES = {
    table: text(
        f"DELETE FROM {table} WHERE project_id = :project_id AND bucket_start < :bucket_cutoff"
    )
    for table in _HOURLY_TABLES
}


async def delete_hourly_rollups(
    db: AsyncSession, project_id: uuid.UUID, bucket_cutoff: datetime
) -> None:
    """Delete the project's rows of every hourly table with ``bucket_start < bucket_cutoff``.

    Does not commit.
    """
    params = {"project_id": project_id, "bucket_cutoff": bucket_cutoff}
    for statement in _DELETES.values():
        await db.execute(statement, params)

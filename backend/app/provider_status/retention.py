"""Retention of the providers' status incidents: two years, whatever any project keeps.

The incidents are public data about the providers, not a tenant's, so they follow no project's
retention. `provider_incidents` has no row-level security, so the retention job deletes without
the worker bypass. Each call deletes at most `batch_size` incidents, oldest first, through
`provider_incidents_started_idx`; the job (`app.jobs.tasks.retention`) gives this class one batch
per opportunity and commits.
"""

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PROVIDER_INCIDENT_RETENTION_DAYS = 730

_DELETE_OLD_INCIDENTS = text(
    """
    DELETE FROM provider_incidents
    WHERE id IN (
        SELECT id FROM provider_incidents
        WHERE started_at < :cutoff
        ORDER BY started_at, id LIMIT :batch_size
    )
    """
)


async def delete_old_incidents(db: AsyncSession, now: datetime, batch_size: int) -> int:
    """Delete up to `batch_size` incidents that started more than
    `PROVIDER_INCIDENT_RETENTION_DAYS` before `now`. Returns how many went. Does not commit."""
    result = await db.execute(
        _DELETE_OLD_INCIDENTS,
        {
            "cutoff": now - timedelta(days=PROVIDER_INCIDENT_RETENTION_DAYS),
            "batch_size": batch_size,
        },
    )
    return int(getattr(result, "rowcount", 0))

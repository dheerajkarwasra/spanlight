"""Retention of the providers' status incidents: two years, whatever any project keeps.

The incidents are public data about the providers, not a tenant's, so they follow no project's
retention. `provider_incidents` has no row-level security, so the retention job deletes without
the worker bypass. Three providers over two years is on the order of 3,000 rows, so one
statement per run is enough; it reads `provider_incidents_started_idx`.
"""

from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import ProviderIncident

PROVIDER_INCIDENT_RETENTION_DAYS = 730


async def apply_provider_incident_retention(
    session_factory: async_sessionmaker[AsyncSession], *, now: datetime
) -> int:
    """Delete incidents that started more than `PROVIDER_INCIDENT_RETENTION_DAYS` before `now`.

    Returns how many went.
    """
    cutoff = now - timedelta(days=PROVIDER_INCIDENT_RETENTION_DAYS)
    async with session_factory() as db:
        result = await db.execute(
            delete(ProviderIncident)
            .where(ProviderIncident.started_at < cutoff)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    return int(getattr(result, "rowcount", 0))

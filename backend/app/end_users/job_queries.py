"""The user stats refresh job's list of projects, read across every project.

It holds nothing but ids, so it runs with row-level security bypassed, in a transaction it ends
itself (the bypass is local to it). Only worker code imports this module.
"""

import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import bypass_rls

# Projects with a trace that names a user, started in [since, until).
_ACTIVE_PROJECTS = text(
    """
    SELECT p.id
    FROM projects AS p
    WHERE EXISTS (
        SELECT 1 FROM traces AS t
        WHERE t.project_id = p.id AND t.external_user_id IS NOT NULL
          AND t.started_at >= :since AND t.started_at < :until
    )
    ORDER BY p.id
    """
)


async def active_project_ids(db: AsyncSession, since: datetime, until: datetime) -> list[uuid.UUID]:
    """Projects with a user-tagged trace started in `[since, until)`.

    Holds nothing but ids, so it runs with row-level security bypassed, in a transaction it ends
    itself (the bypass is local to it). For the refresh job only.
    """
    await bypass_rls(db)
    rows = (await db.execute(_ACTIVE_PROJECTS, {"since": since, "until": until})).scalars().all()
    await db.commit()
    return [uuid.UUID(str(project_id)) for project_id in rows]

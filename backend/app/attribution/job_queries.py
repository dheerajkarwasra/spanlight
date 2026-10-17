"""The attribution refresh job's reads across projects. Only worker and CLI code imports this.

The list of projects holds nothing but ids, so it runs with row-level security bypassed, in a
transaction it ends itself (the bypass is local to it). The promoted keys come from `projects`,
a table without row-level security, so that read needs no bypass; it lives here because only
the job and the CLI ask for them by project id.
"""

import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import bypass_rls

# Projects with a trace started in [since, until).
_ACTIVE_PROJECTS = text(
    """
    SELECT p.id
    FROM projects AS p
    WHERE EXISTS (
        SELECT 1 FROM traces AS t
        WHERE t.project_id = p.id AND t.started_at >= :since AND t.started_at < :until
    )
    ORDER BY p.id
    """
)

_PROMOTED_KEYS = text("SELECT promoted_metadata_keys FROM projects WHERE id = :project_id")


async def active_project_ids(db: AsyncSession, since: datetime, until: datetime) -> list[uuid.UUID]:
    """Projects with a trace started in `[since, until)`.

    Holds nothing but ids, so it runs with row-level security bypassed, in a transaction it ends
    itself (the bypass is local to it). For the refresh job only.
    """
    await bypass_rls(db)
    rows = (await db.execute(_ACTIVE_PROJECTS, {"since": since, "until": until})).scalars().all()
    await db.commit()
    return [uuid.UUID(str(project_id)) for project_id in rows]


async def promoted_keys_of(db: AsyncSession, project_id: uuid.UUID) -> list[str] | None:
    """The project's promoted metadata keys, in the order set; None when there is no project."""
    keys = (await db.execute(_PROMOTED_KEYS, {"project_id": project_id})).scalar_one_or_none()
    return None if keys is None else [str(key) for key in keys]

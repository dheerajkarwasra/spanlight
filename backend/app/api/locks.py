"""Row locks a project route takes before it writes, in the order deletions use."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Access
from app.core.errors import not_found
from app.services.deletion import lock_project_for_write


async def lock_project_of(access: Access, db: AsyncSession) -> uuid.UUID:
    """The route's project, `FOR NO KEY UPDATE` after its organization, for a write that audits.

    `404` when either is gone: a concurrent deletion won. See the lock order in
    `app.services.deletion`.
    """
    project = access.require_project()
    if not await lock_project_for_write(db, access.org.id, project.id):
        raise not_found()
    return project.id

"""Row-level security helpers.

Both settings are transaction-local (`set_config(..., is_local => true)`, the
function form of `SET LOCAL`), so they cannot leak to the next user of a
pooled connection.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def bind_project(session: AsyncSession, project_id: uuid.UUID) -> None:
    """Restrict every table under row-level security to one project for this transaction."""
    await session.execute(
        text("SELECT set_config('app.project_id', :project_id, true)"),
        {"project_id": str(project_id)},
    )


async def bypass_rls(session: AsyncSession) -> None:
    """Make every project's rows visible in every RLS table for this transaction.

    Only worker/maintenance code may call this (retention, demo budget checks).
    Request handlers must use `bind_project` instead.
    """
    await session.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))

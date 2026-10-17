"""Reading `telemetry_losses_hourly` and making projects, for the loss flush tests."""

import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bind_project
from tests.rls.factories import create_project


async def read_losses(
    sessions: async_sessionmaker[AsyncSession], project_id: uuid.UUID
) -> dict[tuple[str, str, datetime], int]:
    """The project's loss rows, read under its own binding as the app role."""
    async with sessions() as db, db.begin():
        await bind_project(db, project_id)
        rows = await db.execute(
            text("SELECT source, reason, bucket_start, count FROM telemetry_losses_hourly")
        )
        return {(row.source, row.reason, row.bucket_start): row.count for row in rows}


async def two_projects(sessions: async_sessionmaker[AsyncSession]) -> list[uuid.UUID]:
    """Two fresh projects, in the order the flush writes them (by id)."""
    async with sessions() as db, db.begin():
        return sorted([await create_project(db), await create_project(db)], key=str)

"""Drain helpers for tests that only care that expired rows go.

`drain` serves one cleanup class for every scope until a batch deletes nothing, through the same
`delete_batch` the job uses, without a job or its fence. The registered job never drains: it takes
one batch per opportunity (`app.jobs.tasks.retention`).
"""

import uuid
from datetime import datetime

from sqlalchemy import text

from app.jobs.tasks.retention import delete_batch
from app.jobs.tasks.retention_cursor import GLOBAL_CLASSES, SOURCE_CLASSES
from tests.jobs.bypass_support import SessionFactory


async def _scopes(session_factory: SessionFactory, cleanup_class: str) -> list[uuid.UUID | None]:
    if cleanup_class in GLOBAL_CLASSES:
        return [None]
    table = "billing_sources" if cleanup_class in SOURCE_CLASSES else "projects"
    async with session_factory() as db:
        return list(await db.scalars(text(f"SELECT id FROM {table} ORDER BY id")))


async def drain(
    session_factory: SessionFactory, cleanup_class: str, now: datetime, *, audit_days: int = 0
) -> int:
    """Delete every eligible row of `cleanup_class`, one committed batch at a time; returns how
    many went (parents, for traces)."""
    total = 0
    for scope in await _scopes(session_factory, cleanup_class):
        while True:
            async with session_factory() as db:
                deleted = await delete_batch(db, cleanup_class, scope, now, audit_days=audit_days)
                await db.commit()
            if not deleted:
                break
            total += deleted
    return total


async def apply_retention(session_factory: SessionFactory, *, now: datetime) -> int:
    """Every project's expired traces, then its expired hourly tables; returns traces deleted."""
    deleted = await drain(session_factory, "trace_cascades", now)
    await drain(session_factory, "hourly_aggregates", now)
    return deleted

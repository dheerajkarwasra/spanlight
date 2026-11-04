"""Whole-trace mutation capture shared by ingestion and operator usage correction."""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.budgets.buckets import bump_hours_of_traces
from app.db.rls import bind_project
from app.rollups.coverage import lock_aggregate_sources
from app.rollups.dirty import mark_dirty_hours


async def lock_trace_identities(
    db: AsyncSession, project_id: uuid.UUID, trace_ids: Sequence[str]
) -> bool:
    """Serialize absent identities too, before reading any trace's old state."""
    await bind_project(db, project_id)
    if (
        await db.scalar(
            text("SELECT id FROM projects WHERE id = :project_id FOR KEY SHARE"),
            {"project_id": project_id},
        )
        is None
    ):
        return False
    await lock_identities(db, project_id, trace_ids)
    return True


async def lock_identities(
    db: AsyncSession, project_id: uuid.UUID, trace_ids: Sequence[str]
) -> None:
    """Lock every identity, then its trace row. The caller has bound the project and holds
    `KEY SHARE` on it.

    The advisory locks are taken in one statement, in sorted key order (`unnest` yields array
    order), so lock ordering is the same as one statement per key. One `statement_timeout`
    therefore bounds the waits for all the locks together, not each lock's wait on its own.
    """
    ordered = sorted(set(trace_ids))
    if not ordered:
        return
    await db.execute(
        text(
            "SELECT count(pg_advisory_xact_lock(hashtextextended(k, 0))) "
            "FROM unnest(CAST(:keys AS text[])) AS u(k)"
        ),
        {"keys": [f"aggregate-trace-identity:{project_id}:{trace_id}" for trace_id in ordered]},
    )
    await db.execute(
        text(
            "SELECT trace_id FROM traces WHERE project_id = :project_id "
            "AND trace_id = ANY(:trace_ids) ORDER BY trace_id FOR UPDATE"
        ),
        {"project_id": project_id, "trace_ids": ordered},
    )


async def trace_hours(
    db: AsyncSession, project_id: uuid.UUID, trace_ids: Sequence[str]
) -> list[datetime]:
    """Return all span and trace-start UTC hours for the locked trace set."""
    if not trace_ids:
        return []
    return list(
        await db.scalars(
            text("""
            SELECT date_trunc('hour', started_at, 'UTC') AS hour FROM traces
            WHERE project_id = :project_id AND trace_id = ANY(:trace_ids)
            UNION
            SELECT date_trunc('hour', started_at, 'UTC') FROM spans
            WHERE project_id = :project_id AND trace_id = ANY(:trace_ids)
            ORDER BY hour
        """),
            {"project_id": project_id, "trace_ids": list(trace_ids)},
        )
    )


async def delete_corrective_traces(
    db: AsyncSession, project_id: uuid.UUID, trace_ids: Sequence[str]
) -> int:
    """Private intentional subtraction; retention/erasure must use source-loss evidence instead."""
    if not trace_ids or not await lock_aggregate_sources(db, project_id):
        return 0
    if not await lock_trace_identities(db, project_id, trace_ids):
        return 0
    hours = await bump_hours_of_traces(db, project_id, trace_ids)
    deleted = list(
        await db.scalars(
            text(
                "DELETE FROM traces WHERE project_id = :project_id AND trace_id = ANY(:trace_ids) "
                "RETURNING trace_id"
            ),
            {"project_id": project_id, "trace_ids": list(trace_ids)},
        )
    )
    await mark_dirty_hours(db, project_id, hours)
    return len(deleted)

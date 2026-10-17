"""Operator backfill of `spans.usage_rollup` and the trace totals (Bet 4 spec §5.4).

Migration 0900 adds the flag without a backfill: rows stored before it keep `false` until a
batch touches their trace. This module recomputes the traces started since a given instant, the
way ingestion does after every batch (`app.ingest.store._recompute_rollups`), one transaction per
batch of traces of one project. Both statements are pure functions of the stored rows, so a
second run, or a run that stopped halfway, changes nothing it already corrected.

Listing the traces across projects needs the row-level security bypass, so only the CLI may
import this module (`tests/rls/test_bypass_callers.py`). Each batch is bound to its project.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from itertools import groupby

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bind_project, bypass_rls
from app.ingest.store import _recompute_rollups

_LIST = text(
    "SELECT project_id, trace_id FROM traces WHERE started_at >= :since "
    "ORDER BY project_id, trace_id"
)
# Ingestion holds the trace rows while it recomputes; taking the same locks first, in key order,
# orders a batch before or after any ingestion of the same traces rather than interleaved with it.
_LOCK = text(
    "SELECT trace_id FROM traces WHERE project_id = :project_id AND trace_id = ANY(:trace_ids) "
    "ORDER BY trace_id FOR UPDATE"
)


async def backfill_usage_rollups(
    session_factory: async_sessionmaker[AsyncSession], *, since: datetime, batch_size: int = 500
) -> int:
    """Recompute the usage rollups and totals of every trace started at or after `since`.

    Returns the number of traces recomputed. A trace deleted between the listing and its batch
    is skipped by the statements themselves.
    """
    async with session_factory() as session:
        await bypass_rls(session)
        rows = (await session.execute(_LIST, {"since": since})).all()
    count = 0
    for project_id, group in groupby(rows, key=lambda row: row.project_id):
        trace_ids = [row.trace_id for row in group]
        for start in range(0, len(trace_ids), batch_size):
            batch = trace_ids[start : start + batch_size]
            async with session_factory() as session:
                await _recompute_batch(session, project_id, batch)
            count += len(batch)
    return count


async def _recompute_batch(
    session: AsyncSession, project_id: uuid.UUID, trace_ids: Sequence[str]
) -> None:
    await bind_project(session, project_id)
    await session.execute(_LOCK, {"project_id": project_id, "trace_ids": list(trace_ids)})
    await _recompute_rollups(session, project_id, trace_ids)
    await session.commit()

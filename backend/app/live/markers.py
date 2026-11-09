"""Per-project change markers: the only caller of the `live_markers` SQL function.

A marker is `(row_count, generation_sum)` over a project's dirty-hour and dirty-shard rows.
Any difference means the project changed: a higher generation is a new ingest, a lower one
committed late changes the pair too, and a repair-job deletion costs one extra refetch.
"""

import uuid
from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

Marker = tuple[int, Decimal]

_READ = text(
    "SELECT project_id, row_count, generation_sum FROM public.live_markers(CAST(:ids AS uuid[]))"
)


async def read_markers(db: AsyncSession, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Marker]:
    """Markers of the given projects; a project with no dirty rows is absent from the result.

    Runs in its own short transaction with a 1 s statement budget. Callers pass only ids a
    subscribed, authorized session asked for: the function reads past row-level security.
    """
    async with db.begin():
        await db.execute(text("SET LOCAL statement_timeout = '1s'"))
        rows = await db.execute(_READ, {"ids": list(ids)})
        return {pid: (count, total) for pid, count, total in rows}

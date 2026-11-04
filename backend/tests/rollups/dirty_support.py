"""Read real dirty-row identities across the rolling-deploy tables."""

from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession


async def dirty_rows(db: AsyncSession) -> list[Row[Any]]:
    return list(
        (
            await db.execute(
                text("""
                    SELECT project_id, bucket_hour, -1 AS shard, generation,
                           first_marked_at, pending_consumers FROM aggregate_dirty_hours
                    UNION ALL
                    SELECT project_id, bucket_hour, shard, generation,
                           first_marked_at, pending_consumers FROM aggregate_dirty_shards
                    ORDER BY project_id, bucket_hour, shard
                """)
            )
        ).all()
    )


async def dirty_row(db: AsyncSession, *, hour: datetime | None = None) -> Row[Any]:
    rows = await dirty_rows(db)
    if hour is not None:
        rows = [row for row in rows if row.bucket_hour == hour]
    (row,) = rows
    return row


async def dirty_count(db: AsyncSession) -> int:
    return len(await dirty_rows(db))


async def set_dirty_pending(db: AsyncSession, consumers: int) -> None:
    """Set fixture masks in both tables, retaining the session's tenant scope."""
    await db.execute(
        text("UPDATE aggregate_dirty_hours SET pending_consumers=:mask"), {"mask": consumers}
    )
    await db.execute(
        text("UPDATE aggregate_dirty_shards SET pending_consumers=:mask"), {"mask": consumers}
    )

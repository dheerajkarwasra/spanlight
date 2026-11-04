"""Durable correction obligations; all effects belong to the caller's transaction."""

import random
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

HOURLY = 1
COST_DAILY = 2
ATTRIBUTION = 4
USER_DAILY = 8
ALL = 15
DIRTY_SHARDS = 16
WRITE_SHARDS = False  # Release 2 sets True; see sharded dirty-hours spec section 4.


def _hour(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("dirty hours must be timezone-aware")
    return value.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


async def mark_dirty_hours(
    db: AsyncSession, project_id: uuid.UUID, hours: Sequence[datetime]
) -> None:
    """Reset every obligation with a new token, retaining the original pending age.

    Call at most once per transaction so all writers retain the same hour lock order.
    """
    normalized = sorted({_hour(hour) for hour in hours})
    if not normalized:
        return
    if WRITE_SHARDS:
        shard = random.randrange(DIRTY_SHARDS)  # noqa: S311 - load spreading, not a secret
        await db.execute(
            text("""
                INSERT INTO aggregate_dirty_shards (project_id, bucket_hour, shard, generation)
                SELECT :project_id, hour, :shard, nextval('aggregate_dirty_generation_seq')
                FROM unnest(CAST(:hours AS timestamptz[])) hour ORDER BY hour
                ON CONFLICT (project_id, bucket_hour, shard) DO UPDATE
                SET generation = nextval('aggregate_dirty_generation_seq'), pending_consumers = 15
            """),
            {
                "project_id": project_id,
                "hours": normalized,
                "shard": shard,
            },
        )
        return
    await db.execute(
        text("""
            INSERT INTO aggregate_dirty_hours (project_id, bucket_hour, generation)
            SELECT :project_id, hour, nextval('aggregate_dirty_generation_seq')
            FROM unnest(CAST(:hours AS timestamptz[])) hour ORDER BY hour
            ON CONFLICT (project_id, bucket_hour) DO UPDATE
            SET generation = nextval('aggregate_dirty_generation_seq'), pending_consumers = 15
        """),
        {"project_id": project_id, "hours": normalized},
    )


async def complete_dirty_consumers(
    db: AsyncSession,
    project_id: uuid.UUID,
    hour: datetime,
    generation: int,
    consumers: int,
    *,
    shard: int = -1,
) -> bool:
    """Clear completed/classified bits for this token; shard -1 addresses the old table."""
    if type(consumers) is not int or not 0 < consumers <= ALL:
        raise ValueError("consumers must be a nonzero subset of ALL")
    table = "aggregate_dirty_hours" if shard == -1 else "aggregate_dirty_shards"
    shard_filter = "" if shard == -1 else "AND shard = :shard"
    matched = await db.scalar(
        text(f"""
            UPDATE {table} SET pending_consumers = pending_consumers & ~:consumers
            WHERE project_id = :project_id AND bucket_hour = :hour AND generation = :generation
              {shard_filter}
            RETURNING true
        """),
        {
            "project_id": project_id,
            "hour": _hour(hour),
            "generation": generation,
            "consumers": consumers,
            "shard": shard,
        },
    )
    return matched is True


async def ack_dirty_hour(
    db: AsyncSession,
    project_id: uuid.UUID,
    hour: datetime,
    generation: int,
    *,
    shard: int = -1,
) -> bool:
    """Acknowledge an unchanged, empty row; shard -1 addresses the old table."""
    table = "aggregate_dirty_hours" if shard == -1 else "aggregate_dirty_shards"
    shard_filter = "" if shard == -1 else "AND shard = :shard"
    matched = await db.scalar(
        text(f"""
            DELETE FROM {table}
            WHERE project_id = :project_id AND bucket_hour = :hour AND generation = :generation
              {shard_filter}
              AND pending_consumers = 0
            RETURNING true
        """),
        {"project_id": project_id, "hour": _hour(hour), "generation": generation, "shard": shard},
    )
    return matched is True

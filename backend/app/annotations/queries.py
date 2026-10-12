"""Queue reads and writes. Every query runs in a transaction bound to the project, so row-level
security filters it; the `project_id` and `queue_id` conditions are the second line."""

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import bindparam, delete, exists, func, or_, select, text
from sqlalchemy.dialects.postgresql import ARRAY, TEXT, UUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AnnotationQueue, AnnotationQueueItem, Trace
from app.db.models.annotations import QueueItemStatus

CLAIM_TIMEOUT = timedelta(minutes=30)

_INSERT_ITEMS = text(
    """
    INSERT INTO annotation_queue_items (id, queue_id, trace_id, span_id)
    SELECT i, :queue_id, t, :span_id FROM unnest(:ids, :traces) AS u(i, t)
    ON CONFLICT (queue_id, trace_id, coalesce(span_id, '')) DO NOTHING
    """
).bindparams(
    bindparam("ids", type_=ARRAY(UUID(as_uuid=True))),
    bindparam("traces", type_=ARRAY(TEXT)),
    bindparam("queue_id", type_=UUID(as_uuid=True)),
)


async def list_queues(
    db: AsyncSession, project_id: uuid.UUID, *, after: uuid.UUID | None, limit: int
) -> list[AnnotationQueue]:
    """Newest first (ids are time-ordered), up to `limit + 1` rows."""
    query = (
        select(AnnotationQueue)
        .where(AnnotationQueue.project_id == project_id)
        .order_by(AnnotationQueue.id.desc())
        .limit(limit + 1)
    )
    if after is not None:
        query = query.where(AnnotationQueue.id < after)
    return list((await db.scalars(query)).all())


async def get_queue(
    db: AsyncSession, project_id: uuid.UUID, queue_id: uuid.UUID
) -> AnnotationQueue | None:
    return await db.scalar(
        select(AnnotationQueue).where(
            AnnotationQueue.project_id == project_id, AnnotationQueue.id == queue_id
        )
    )


async def progress_of(
    db: AsyncSession, queue_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, dict[str, int]]:
    """Item counts per status for each queue, in one query; absent statuses are zero."""
    result = {queue_id: {status.value: 0 for status in QueueItemStatus} for queue_id in queue_ids}
    rows = await db.execute(
        select(AnnotationQueueItem.queue_id, AnnotationQueueItem.status, func.count())
        .where(AnnotationQueueItem.queue_id.in_(queue_ids))
        .group_by(AnnotationQueueItem.queue_id, AnnotationQueueItem.status)
    )
    for queue_id, status, count in rows:
        result[queue_id][status] = int(count)
    return result


async def queue_uses_config(db: AsyncSession, project_id: uuid.UUID, config_id: uuid.UUID) -> bool:
    """Whether any queue of the project asks for the score config `config_id`."""
    found = await db.scalar(
        select(
            exists().where(
                AnnotationQueue.project_id == project_id,
                AnnotationQueue.score_config_ids.contains([config_id]),
            )
        )
    )
    return bool(found)


async def known_trace_ids(
    db: AsyncSession, project_id: uuid.UUID, trace_ids: Sequence[str]
) -> set[str]:
    rows = await db.scalars(
        select(Trace.trace_id).where(Trace.project_id == project_id, Trace.trace_id.in_(trace_ids))
    )
    return set(rows)


async def insert_items(
    db: AsyncSession,
    queue_id: uuid.UUID,
    item_ids: Sequence[uuid.UUID],
    trace_ids: Sequence[str],
    span_id: str | None,
) -> int:
    """Queue the traces, skipping those already queued; returns how many rows were added."""
    result = await db.execute(
        _INSERT_ITEMS,
        {
            "queue_id": queue_id,
            "ids": list(item_ids),
            "traces": list(trace_ids),
            "span_id": span_id,
        },
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


async def list_items(
    db: AsyncSession,
    queue_id: uuid.UUID,
    *,
    status: QueueItemStatus | None,
    after: uuid.UUID | None,
    limit: int,
) -> list[AnnotationQueueItem]:
    """By id ascending, up to `limit + 1` rows."""
    query = (
        select(AnnotationQueueItem)
        .where(AnnotationQueueItem.queue_id == queue_id)
        .order_by(AnnotationQueueItem.id)
        .limit(limit + 1)
    )
    if status is not None:
        query = query.where(AnnotationQueueItem.status == status.value)
    if after is not None:
        query = query.where(AnnotationQueueItem.id > after)
    return list((await db.scalars(query)).all())


async def claim_candidate(
    db: AsyncSession, queue_id: uuid.UUID, user_id: uuid.UUID, now: datetime
) -> AnnotationQueueItem | None:
    """Lock the lowest-id pending item the caller may take: unassigned, theirs already, or
    claimed so long ago the claim is abandoned. Rows another request holds are skipped."""
    return await db.scalar(
        select(AnnotationQueueItem)
        .where(
            AnnotationQueueItem.queue_id == queue_id,
            AnnotationQueueItem.status == QueueItemStatus.PENDING.value,
            or_(
                AnnotationQueueItem.assigned_to.is_(None),
                AnnotationQueueItem.assigned_to == user_id,
                AnnotationQueueItem.assigned_at < now - CLAIM_TIMEOUT,
            ),
        )
        .order_by(AnnotationQueueItem.id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )


async def lock_item(
    db: AsyncSession, queue_id: uuid.UUID, item_id: uuid.UUID
) -> AnnotationQueueItem | None:
    return await db.scalar(
        select(AnnotationQueueItem)
        .where(AnnotationQueueItem.queue_id == queue_id, AnnotationQueueItem.id == item_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def count_items(db: AsyncSession, queue_id: uuid.UUID) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(AnnotationQueueItem)
        .where(AnnotationQueueItem.queue_id == queue_id)
    )
    return int(count or 0)


async def delete_queue(db: AsyncSession, queue_id: uuid.UUID) -> None:
    await db.execute(delete(AnnotationQueue).where(AnnotationQueue.id == queue_id))

"""Annotation queues: create and delete queues, queue traces, and work items (claim, complete,
skip, release). Callers commit.

Every write runs in a transaction bound to the project (row-level security). Claiming locks the
item row `FOR UPDATE SKIP LOCKED`, so two reviewers claiming at once get different items.
Completing writes the scores through `scores.service.write_scores` as the reviewer's human
scores and marks the item done in the same transaction. Audit metadata holds the queue's name
and item count; never trace content or scores.
"""

import uuid
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.annotations import queries
from app.annotations.errors import (
    ItemNotAssignedError,
    ItemNotPendingError,
    QueueItemNotFoundError,
    QueueNameTakenError,
    QueueNotFoundError,
    ScoreNotInQueueError,
    ScoreRejectedError,
    ScoreRequiredError,
    UnknownScoreConfigError,
    UnknownTraceError,
)
from app.annotations.schemas import CompletedScore, Completion, ItemsIn, QueueSpec
from app.core.ids import new_id
from app.db.errors import violated_constraint
from app.db.models import AnnotationQueue, AnnotationQueueItem, AuditAction
from app.db.models.annotations import QueueItemStatus
from app.scores.schemas import ScoreIn
from app.scores.service import configs_in_project, write_scores
from app.scores.types import ScoreSource
from app.services.audit import record_audit

NAME_CONSTRAINT = "annotation_queues_project_name_key"


async def queue_uses_config(db: AsyncSession, project_id: uuid.UUID, config_id: uuid.UUID) -> bool:
    """Whether a queue of the project asks for this score config. Deleting a config a queue
    needs would leave a queue whose items can never be completed."""
    return await queries.queue_uses_config(db, project_id, config_id)


async def create_queue(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    spec: QueueSpec,
    *,
    now: datetime,
    ip: str | None = None,
) -> AnnotationQueue:
    """Store a queue. Raises `UnknownScoreConfigError` and `QueueNameTakenError`."""
    found = await configs_in_project(db, project_id, spec.score_config_ids)
    if len(found) != len(spec.score_config_ids):
        raise UnknownScoreConfigError
    queue = AnnotationQueue(
        project_id=project_id,
        name=spec.name,
        instructions=spec.instructions,
        score_config_ids=spec.score_config_ids,
        created_by=actor_id,
        created_at=now,
    )
    db.add(queue)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as error:
        if violated_constraint(error) == NAME_CONSTRAINT:
            raise QueueNameTakenError from None
        raise
    await record_audit(
        db,
        org_id=org_id,
        actor_user_id=actor_id,
        action=AuditAction.ANNOTATION_QUEUE_CREATE,
        target_type="annotation_queue",
        target_id=queue.id,
        ip=ip,
        metadata={"name": queue.name},
    )
    return queue


async def delete_queue(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    queue_id: uuid.UUID,
    actor_id: uuid.UUID,
    *,
    ip: str | None = None,
) -> None:
    """Delete the queue with its items. Raises `QueueNotFoundError`."""
    queue = await _queue(db, project_id, queue_id)
    item_count = await queries.count_items(db, queue.id)
    await queries.delete_queue(db, queue.id)
    await record_audit(
        db,
        org_id=org_id,
        actor_user_id=actor_id,
        action=AuditAction.ANNOTATION_QUEUE_DELETE,
        target_type="annotation_queue",
        target_id=queue_id,
        ip=ip,
        metadata={"name": queue.name, "items": item_count},
    )


async def add_items(
    db: AsyncSession, project_id: uuid.UUID, queue_id: uuid.UUID, items: ItemsIn
) -> tuple[int, int]:
    """Queue the traces and return `(added, skipped_duplicates)`.

    Raises `QueueNotFoundError` and `UnknownTraceError` (nothing is queued then).
    """
    await _queue(db, project_id, queue_id)
    unique = list(dict.fromkeys(items.trace_ids))
    known = await queries.known_trace_ids(db, project_id, unique)
    missing = [trace_id for trace_id in unique if trace_id not in known]
    if missing:
        raise UnknownTraceError(missing)
    added = await queries.insert_items(
        db, queue_id, [new_id() for _ in unique], unique, items.span_id
    )
    return added, len(unique) - added


async def claim_next(
    db: AsyncSession,
    project_id: uuid.UUID,
    queue_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    now: datetime,
) -> AnnotationQueueItem | None:
    """Assign the next pending item to the caller and return it, or `None` when there is none.
    Raises `QueueNotFoundError`."""
    await _queue(db, project_id, queue_id)
    item = await queries.claim_candidate(db, queue_id, user_id, now)
    if item is None:
        return None
    item.assigned_to = user_id
    item.assigned_at = now
    await db.flush()
    return item


async def complete_item(
    db: AsyncSession,
    project_id: uuid.UUID,
    queue_id: uuid.UUID,
    item_id: uuid.UUID,
    user_id: uuid.UUID,
    completion: Completion,
    *,
    now: datetime,
) -> AnnotationQueueItem:
    """Write the reviewer's scores on the item's trace and mark it done.

    Raises `QueueNotFoundError`, `QueueItemNotFoundError`, `ItemNotPendingError`,
    `ItemNotAssignedError`, `ScoreNotInQueueError`, `ScoreRequiredError` and
    `ScoreRejectedError`; the caller must not commit after one.
    """
    queue = await _queue(db, project_id, queue_id)
    item = await _owned_item(db, queue_id, item_id, user_id)
    await _check_names(db, project_id, queue, completion)
    outcome = await write_scores(
        db,
        project_id,
        [_score_in(item, score) for score in completion.scores],
        source=ScoreSource.HUMAN,
        author_user_id=user_id,
    )
    if outcome.rejected:
        raise ScoreRejectedError(outcome.rejected[0].reason)
    item.status = QueueItemStatus.DONE.value
    item.completed_by = user_id
    item.completed_at = now
    await db.flush()
    return item


async def skip_item(
    db: AsyncSession,
    project_id: uuid.UUID,
    queue_id: uuid.UUID,
    item_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    now: datetime,
) -> AnnotationQueueItem:
    """Mark the caller's claimed item skipped. Same errors as `complete_item`, bar scores."""
    await _queue(db, project_id, queue_id)
    item = await _owned_item(db, queue_id, item_id, user_id)
    item.status = QueueItemStatus.SKIPPED.value
    item.completed_by = user_id
    item.completed_at = now
    await db.flush()
    return item


async def release_item(
    db: AsyncSession,
    project_id: uuid.UUID,
    queue_id: uuid.UUID,
    item_id: uuid.UUID,
    user_id: uuid.UUID,
) -> AnnotationQueueItem:
    """Give the caller's claimed item back to the queue. Same errors as `skip_item`."""
    await _queue(db, project_id, queue_id)
    item = await _owned_item(db, queue_id, item_id, user_id)
    item.assigned_to = None
    item.assigned_at = None
    await db.flush()
    return item


async def _queue(db: AsyncSession, project_id: uuid.UUID, queue_id: uuid.UUID) -> AnnotationQueue:
    queue = await queries.get_queue(db, project_id, queue_id)
    if queue is None:
        raise QueueNotFoundError
    return queue


async def _owned_item(
    db: AsyncSession, queue_id: uuid.UUID, item_id: uuid.UUID, user_id: uuid.UUID
) -> AnnotationQueueItem:
    """The item, row-locked, if it is pending and claimed by the caller."""
    item = await queries.lock_item(db, queue_id, item_id)
    if item is None:
        raise QueueItemNotFoundError
    if item.status != QueueItemStatus.PENDING.value:
        raise ItemNotPendingError
    if item.assigned_to != user_id:
        raise ItemNotAssignedError
    return item


async def _check_names(
    db: AsyncSession, project_id: uuid.UUID, queue: AnnotationQueue, completion: Completion
) -> None:
    """Every submitted name is one of the queue's configs and every config has a score."""
    configs = await configs_in_project(db, project_id, queue.score_config_ids)
    expected = {config.name for config in configs}
    given = {score.name for score in completion.scores}
    if outside := sorted(given - expected):
        raise ScoreNotInQueueError(outside)
    if absent := sorted(expected - given):
        raise ScoreRequiredError(absent)


def _score_in(item: AnnotationQueueItem, score: CompletedScore) -> ScoreIn:
    return ScoreIn(
        trace_id=item.trace_id,
        span_id=item.span_id,
        name=score.name,
        value=score.value,
        string_value=score.string_value,
        comment=score.comment,
    )

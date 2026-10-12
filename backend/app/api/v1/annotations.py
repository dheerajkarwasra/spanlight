"""Annotation queues: lists of traces for people to score.

Members list and read queues (`project:read`); members create and delete queues, queue traces
and work items (`annotations:write`). A reviewer claims the next item, then completes, skips or
releases it. Responses are built before the commit, which ends the project binding that
row-level security needs. A failed complete never commits, so its scores are not written.
"""

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.annotations import queries, service
from app.api.annotation_problems import QUEUE_ERRORS, queue_problem
from app.api.deps import Access, ClockDep, DbSession, client_ip, require
from app.api.locks import lock_project_of
from app.api.schemas import Page
from app.api.schemas.annotations import (
    Completion,
    ItemsAddedOut,
    ItemsIn,
    QueueItemOut,
    QueueOut,
    QueueSpec,
)
from app.core.errors import not_found
from app.core.pagination import PageLimit, decode_uuid_cursor, encode_cursor
from app.core.permissions import Permission
from app.db.models.annotations import QueueItemStatus

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/projects/{project_id}/annotation-queues", tags=["annotations"])

QueueReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]
QueueWriter = Annotated[Access, Depends(require(Permission.ANNOTATIONS_WRITE))]

DEFAULT_PAGE = 50


def _log(event: str, access: Access, queue_id: uuid.UUID, **fields: object) -> None:
    logger.info(
        event,
        org_id=str(access.org.id),
        project_id=str(access.require_project().id),
        queue_id=str(queue_id),
        **fields,
    )


@router.get("", response_model=Page[QueueOut], summary="List annotation queues")
async def list_queues(
    project_id: uuid.UUID,
    access: QueueReader,
    db: DbSession,
    limit: PageLimit = DEFAULT_PAGE,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[QueueOut]:
    """Newest first, each with its progress."""
    after = decode_uuid_cursor(cursor)[1] if cursor else None
    rows = await queries.list_queues(db, access.require_project().id, after=after, limit=limit)
    shown = rows[:limit]
    progress = await queries.progress_of(db, [queue.id for queue in shown]) if shown else {}
    last = shown[-1] if shown else None
    return Page(
        items=[QueueOut.of(queue, progress[queue.id]) for queue in shown],
        next_cursor=encode_cursor(last.created_at, str(last.id))
        if len(rows) > limit and last
        else None,
    )


@router.post(
    "", status_code=status.HTTP_201_CREATED, response_model=QueueOut, summary="Add a queue"
)
async def create_queue(
    project_id: uuid.UUID,
    body: QueueSpec,
    request: Request,
    access: QueueWriter,
    db: DbSession,
    clock: ClockDep,
) -> QueueOut:
    """Store an empty queue. `409 NAME_TAKEN`, `422 UNKNOWN_SCORE_CONFIG`."""
    locked_project_id = await lock_project_of(access, db)
    try:
        queue = await service.create_queue(
            db,
            access.org.id,
            locked_project_id,
            access.user_id,
            body,
            now=clock(),
            ip=client_ip(request),
        )
    except QUEUE_ERRORS as error:
        raise queue_problem(error) from None
    created = QueueOut.of(queue, {"pending": 0, "done": 0, "skipped": 0})
    await db.commit()
    _log("annotation_queue_created", access, queue.id)
    return created


@router.get("/{queue_id}", response_model=QueueOut, summary="Get a queue")
async def get_queue(
    project_id: uuid.UUID, queue_id: uuid.UUID, access: QueueReader, db: DbSession
) -> QueueOut:
    queue = await queries.get_queue(db, access.require_project().id, queue_id)
    if queue is None:
        raise not_found()
    return QueueOut.of(queue, (await queries.progress_of(db, [queue.id]))[queue.id])


@router.delete("/{queue_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a queue")
async def delete_queue(
    project_id: uuid.UUID,
    queue_id: uuid.UUID,
    request: Request,
    access: QueueWriter,
    db: DbSession,
) -> None:
    """Delete the queue with its items; the scores already given stay."""
    locked_project_id = await lock_project_of(access, db)
    try:
        await service.delete_queue(
            db, access.org.id, locked_project_id, queue_id, access.user_id, ip=client_ip(request)
        )
    except QUEUE_ERRORS as error:
        raise queue_problem(error) from None
    await db.commit()
    _log("annotation_queue_deleted", access, queue_id)


@router.post("/{queue_id}/items", response_model=ItemsAddedOut, summary="Queue traces")
async def add_items(
    project_id: uuid.UUID,
    queue_id: uuid.UUID,
    body: ItemsIn,
    access: QueueWriter,
    db: DbSession,
) -> ItemsAddedOut:
    """Queue up to 200 traces, or the given span of each. A trace already queued is skipped."""
    try:
        added, skipped = await service.add_items(db, access.require_project().id, queue_id, body)
    except QUEUE_ERRORS as error:
        raise queue_problem(error) from None
    await db.commit()
    _log("annotation_items_added", access, queue_id, added=added, skipped=skipped)
    return ItemsAddedOut(added=added, skipped_duplicates=skipped)


@router.get("/{queue_id}/items", response_model=Page[QueueItemOut], summary="List a queue's items")
async def list_items(
    project_id: uuid.UUID,
    queue_id: uuid.UUID,
    access: QueueReader,
    db: DbSession,
    item_status: Annotated[QueueItemStatus | None, Query(alias="status")] = None,
    limit: PageLimit = DEFAULT_PAGE,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[QueueItemOut]:
    """By id, oldest first; `status` keeps one status."""
    if await queries.get_queue(db, access.require_project().id, queue_id) is None:
        raise not_found()
    after = decode_uuid_cursor(cursor)[1] if cursor else None
    rows = await queries.list_items(db, queue_id, status=item_status, after=after, limit=limit)
    shown = rows[:limit]
    last = shown[-1] if shown else None
    return Page(
        items=[QueueItemOut.of(item) for item in shown],
        next_cursor=encode_cursor(last.created_at, str(last.id))
        if len(rows) > limit and last
        else None,
    )


@router.post(
    "/{queue_id}/claim",
    response_model=QueueItemOut,
    responses={204: {"description": "No item is waiting"}},
    summary="Claim the next item",
)
async def claim(
    project_id: uuid.UUID,
    queue_id: uuid.UUID,
    access: QueueWriter,
    db: DbSession,
    clock: ClockDep,
) -> QueueItemOut | Response:
    """The lowest-id pending item that is free, already yours, or claimed over 30 minutes ago;
    `204` when there is none."""
    try:
        item = await service.claim_next(
            db, access.require_project().id, queue_id, access.user_id, now=clock()
        )
    except QUEUE_ERRORS as error:
        raise queue_problem(error) from None
    if item is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    claimed = QueueItemOut.of(item)
    await db.commit()
    return claimed


@router.post(
    "/{queue_id}/items/{item_id}/complete",
    response_model=QueueItemOut,
    summary="Complete an item",
)
async def complete_item(
    project_id: uuid.UUID,
    queue_id: uuid.UUID,
    item_id: uuid.UUID,
    body: Completion,
    access: QueueWriter,
    db: DbSession,
    clock: ClockDep,
) -> QueueItemOut:
    """Score the item's trace: one score for each of the queue's configs, no others.
    `409 ITEM_NOT_ASSIGNED_TO_YOU`, `422 SCORE_NOT_IN_QUEUE`, `422 SCORE_REQUIRED`."""
    try:
        item = await service.complete_item(
            db,
            access.require_project().id,
            queue_id,
            item_id,
            access.user_id,
            body,
            now=clock(),
        )
    except QUEUE_ERRORS as error:
        raise queue_problem(error) from None
    done = QueueItemOut.of(item)
    await db.commit()
    _log("annotation_item_completed", access, queue_id, item_id=str(item_id))
    return done


@router.post(
    "/{queue_id}/items/{item_id}/skip", response_model=QueueItemOut, summary="Skip an item"
)
async def skip_item(
    project_id: uuid.UUID,
    queue_id: uuid.UUID,
    item_id: uuid.UUID,
    access: QueueWriter,
    db: DbSession,
    clock: ClockDep,
) -> QueueItemOut:
    """Mark your claimed item skipped. `409 ITEM_NOT_ASSIGNED_TO_YOU`."""
    try:
        item = await service.skip_item(
            db, access.require_project().id, queue_id, item_id, access.user_id, now=clock()
        )
    except QUEUE_ERRORS as error:
        raise queue_problem(error) from None
    skipped = QueueItemOut.of(item)
    await db.commit()
    return skipped


@router.post(
    "/{queue_id}/items/{item_id}/release", response_model=QueueItemOut, summary="Release an item"
)
async def release_item(
    project_id: uuid.UUID,
    queue_id: uuid.UUID,
    item_id: uuid.UUID,
    access: QueueWriter,
    db: DbSession,
) -> QueueItemOut:
    """Give your claimed item back to the queue. `409 ITEM_NOT_ASSIGNED_TO_YOU`."""
    try:
        item = await service.release_item(
            db, access.require_project().id, queue_id, item_id, access.user_id
        )
    except QUEUE_ERRORS as error:
        raise queue_problem(error) from None
    released = QueueItemOut.of(item)
    await db.commit()
    return released

"""The review policy: what a project queues for review every UTC night, and its history; and
the failing-span rollup of what reviewers labelled.

Members read the policy, the selection history and the rollup (`project:read`); members set,
delete and run the policy by hand (`annotations:write`). Sessions and personal access tokens
only: an API key is refused by `require()`. Responses are built before the commit, which ends
the project binding that row-level security needs.
"""

import uuid
from datetime import UTC, date, datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, Request, status

from app.api.deps import Access, ClockDep, DbSession, client_ip, require
from app.api.locks import lock_project_of
from app.api.review_problems import REVIEW_ERRORS, review_problem
from app.api.schemas import Page
from app.api.schemas.review import (
    FailingSpansOut,
    ReviewPolicyIn,
    ReviewPolicyOut,
    RunRequest,
    RunRequestedOut,
    SelectionOut,
)
from app.api.window import Window
from app.core.pagination import decode_cursor, encode_cursor
from app.core.permissions import Permission
from app.db.models import ReviewPolicy
from app.review import queries, service
from app.review.calendar import next_run_at
from app.review.failing_spans import failing_spans

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/projects/{project_id}", tags=["review"])

PolicyReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]
PolicyWriter = Annotated[Access, Depends(require(Permission.ANNOTATIONS_WRITE))]

MAX_SELECTIONS_PAGE = 30
SelectionsLimit = Annotated[int, Query(ge=1, le=MAX_SELECTIONS_PAGE)]


def _log(event: str, access: Access, **fields: object) -> None:
    logger.info(
        event, org_id=str(access.org.id), project_id=str(access.require_project().id), **fields
    )


async def _policy_out(db: DbSession, policy: ReviewPolicy, now: datetime) -> ReviewPolicyOut:
    queue = await queries.queue_in_project(db, policy.project_id, policy.queue_id)
    return ReviewPolicyOut.of(
        policy,
        # The queue cannot be deleted while the policy exists (`QUEUE_IN_USE`).
        queue_name=queue.name if queue else "",
        next_run_at=next_run_at(now, enabled=policy.enabled),
        last_selection=await queries.latest_selection(db, policy.project_id),
    )


@router.get("/review-policy", response_model=ReviewPolicyOut, summary="Get the review policy")
async def get_policy(
    project_id: uuid.UUID, access: PolicyReader, db: DbSession, clock: ClockDep
) -> ReviewPolicyOut:
    """The project's policy with its next run and last selection. `404` when it has none."""
    try:
        policy = await service.get_policy(db, access.require_project().id)
    except REVIEW_ERRORS as error:
        raise review_problem(error) from None
    return await _policy_out(db, policy, clock())


@router.put("/review-policy", response_model=ReviewPolicyOut, summary="Set the review policy")
async def put_policy(
    project_id: uuid.UUID,
    body: ReviewPolicyIn,
    request: Request,
    access: PolicyWriter,
    db: DbSession,
    clock: ClockDep,
) -> ReviewPolicyOut:
    """Create or replace the policy; omitted fields take their defaults. `404` when the queue
    is not this project's, `422 QUEUE_LACKS_FAILURE_CATEGORY`, `422 NOTHING_SELECTED`."""
    locked_project_id = await lock_project_of(access, db)
    now = clock()
    try:
        policy = await service.put_policy(
            db,
            access.org.id,
            locked_project_id,
            access.user_id,
            body,
            now=now,
            ip=client_ip(request),
        )
    except REVIEW_ERRORS as error:
        raise review_problem(error) from None
    saved = await _policy_out(db, policy, now)
    await db.commit()
    _log("review_policy_set", access, policy_id=str(policy.id), enabled=policy.enabled)
    return saved


@router.delete(
    "/review-policy", status_code=status.HTTP_204_NO_CONTENT, summary="Delete the review policy"
)
async def delete_policy(
    project_id: uuid.UUID, request: Request, access: PolicyWriter, db: DbSession
) -> None:
    """Delete the policy; the selection history stays."""
    locked_project_id = await lock_project_of(access, db)
    try:
        await service.delete_policy(
            db, access.org.id, locked_project_id, access.user_id, ip=client_ip(request)
        )
    except REVIEW_ERRORS as error:
        raise review_problem(error) from None
    await db.commit()
    _log("review_policy_deleted", access)


@router.post(
    "/review-policy/runs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunRequestedOut,
    summary="Select a day now",
)
async def request_run(
    project_id: uuid.UUID,
    body: RunRequest,
    request: Request,
    access: PolicyWriter,
    db: DbSession,
    clock: ClockDep,
) -> RunRequestedOut:
    """Queue the selection of a completed UTC day, yesterday to 7 days back.
    `422 DAY_NOT_COMPLETE`, `422 DAY_TOO_OLD`, `409 ALREADY_SELECTED`."""
    locked_project_id = await lock_project_of(access, db)
    try:
        job_id = await service.request_run(
            db,
            access.org.id,
            locked_project_id,
            access.user_id,
            body.day,
            now=clock(),
            ip=client_ip(request),
        )
    except REVIEW_ERRORS as error:
        raise review_problem(error) from None
    await db.commit()
    _log("review_run_requested", access, day=body.day.isoformat(), job_id=job_id)
    return RunRequestedOut(day=body.day, job_id=job_id)


@router.get(
    "/review-policy/selections", response_model=Page[SelectionOut], summary="List selections"
)
async def list_selections(
    project_id: uuid.UUID,
    access: PolicyReader,
    db: DbSession,
    limit: SelectionsLimit = MAX_SELECTIONS_PAGE,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[SelectionOut]:
    """Newest day first; the history outlives the policy and its queue (`queue_id` is null
    once the queue is gone)."""
    before = decode_cursor(cursor)[0].astimezone(UTC).date() if cursor else None
    rows = await service.list_selections(
        db, access.require_project().id, before=before, limit=limit
    )
    shown = rows[:limit]
    last = shown[-1] if shown else None
    return Page(
        items=[SelectionOut.of(row) for row in shown],
        next_cursor=encode_cursor(_midnight(last.day), last.day.isoformat())
        if len(rows) > limit and last
        else None,
    )


@router.get("/review/failing-spans", response_model=FailingSpansOut, summary="List failing spans")
async def list_failing_spans(
    project_id: uuid.UUID, access: PolicyReader, db: DbSession, window: Window
) -> FailingSpansOut:
    """Where the window's reviewed traces first went wrong: each trace's latest
    `failure_category` that names a span, `none` left out, grouped by span name, span kind and
    category, with Wilson 95 % bounds. `422 WINDOW_TOO_LARGE` over 30 days."""
    return await failing_spans(db, access.require_project().id, window)


def _midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)

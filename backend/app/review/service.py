"""The review policy: set, read and delete a project's one policy, and ask for a manual run.
Callers commit.

Every write runs in a transaction bound to the project (row-level security). The queue a policy
fills must be the project's and must ask for the `failure_category` config, so every reviewed
trace gets a category. A manual run enqueues the same per-project job the nightly scheduler
does, under the same dedupe key, so a day is selected once however it was asked for.
"""

import uuid
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditAction, ReviewPolicy, ReviewSelection
from app.jobs.queue import enqueue
from app.review import queries
from app.review.calendar import check_manual_day
from app.review.errors import (
    AlreadySelectedError,
    NothingSelectedError,
    PolicyNotFoundError,
    QueueLacksFailureCategoryError,
    QueueNotInProjectError,
)
from app.review.job_keys import SELECT_PROJECT_JOB, project_job_key
from app.review.schemas import ReviewPolicyIn
from app.scores.failure_category import failure_category_config, queue_asks_failure_category
from app.services.audit import record_audit


async def get_policy(db: AsyncSession, project_id: uuid.UUID) -> ReviewPolicy:
    """The project's policy. Raises `PolicyNotFoundError`."""
    policy = await queries.get_policy(db, project_id)
    if policy is None:
        raise PolicyNotFoundError
    return policy


async def put_policy(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    body: ReviewPolicyIn,
    *,
    now: datetime,
    ip: str | None = None,
) -> ReviewPolicy:
    """Create or replace the policy. Raises `QueueNotInProjectError`,
    `QueueLacksFailureCategoryError` and `NothingSelectedError`."""
    queue = await queries.queue_in_project(db, project_id, body.queue_id)
    if queue is None:
        raise QueueNotInProjectError
    if not queue_asks_failure_category(queue, await failure_category_config(db, project_id)):
        raise QueueLacksFailureCategoryError
    if body.selects_nothing():
        raise NothingSelectedError
    policy, created = await queries.upsert_policy(
        db, project_id, body.model_dump(), actor_id=actor_id, now=now
    )
    await record_audit(
        db,
        org_id=org_id,
        actor_user_id=actor_id,
        action=AuditAction.REVIEW_POLICY_CREATE if created else AuditAction.REVIEW_POLICY_UPDATE,
        target_type="review_policy",
        target_id=policy.id,
        ip=ip,
        metadata={"queue_id": str(policy.queue_id), "enabled": policy.enabled},
    )
    return policy


async def delete_policy(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    *,
    ip: str | None = None,
) -> None:
    """Delete the policy; the selection history stays. Raises `PolicyNotFoundError`."""
    policy = await get_policy(db, project_id)
    await queries.delete_policy(db, project_id)
    await record_audit(
        db,
        org_id=org_id,
        actor_user_id=actor_id,
        action=AuditAction.REVIEW_POLICY_DELETE,
        target_type="review_policy",
        target_id=policy.id,
        ip=ip,
        metadata={"queue_id": str(policy.queue_id)},
    )


async def request_run(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    day: date,
    *,
    now: datetime,
    ip: str | None = None,
) -> int:
    """Enqueue the selection of a completed day and return the job id.

    Raises `PolicyNotFoundError`, `DayNotCompleteError`, `DayTooOldError` and
    `AlreadySelectedError`. A run already queued for the day is returned, not duplicated.
    """
    policy = await get_policy(db, project_id)
    check_manual_day(day, now)
    if await queries.selection_for_day(db, project_id, day) is not None:
        raise AlreadySelectedError
    dedupe_key = project_job_key(project_id, day)
    await enqueue(
        db,
        SELECT_PROJECT_JOB,
        {"project_id": str(project_id), "day": day.isoformat()},
        dedupe_key=dedupe_key,
        max_attempts=3,
    )
    await record_audit(
        db,
        org_id=org_id,
        actor_user_id=actor_id,
        action=AuditAction.REVIEW_POLICY_RUN,
        target_type="review_policy",
        target_id=policy.id,
        ip=ip,
        metadata={"day": day.isoformat()},
    )
    return await queries.job_id_for(db, dedupe_key)


async def list_selections(
    db: AsyncSession, project_id: uuid.UUID, *, before: date | None, limit: int
) -> list[ReviewSelection]:
    """Newest day first, up to `limit + 1` rows; the history outlives the policy and its queue."""
    return await queries.list_selections(db, project_id, before=before, limit=limit)

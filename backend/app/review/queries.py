"""Review policy and selection reads and writes. Every query runs in a transaction bound to the
project, so row-level security filters it; the `project_id` conditions are the second line."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, delete, literal_column, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.db.models import AnnotationQueue, Job, ReviewPolicy, ReviewSelection


async def get_policy(db: AsyncSession, project_id: uuid.UUID) -> ReviewPolicy | None:
    return await db.scalar(select(ReviewPolicy).where(ReviewPolicy.project_id == project_id))


async def policy_for_queue(
    db: AsyncSession, project_id: uuid.UUID, queue_id: uuid.UUID
) -> ReviewPolicy | None:
    """The policy that fills this queue, if one does: the queue cannot be deleted while it does."""
    return await db.scalar(
        select(ReviewPolicy).where(
            ReviewPolicy.project_id == project_id, ReviewPolicy.queue_id == queue_id
        )
    )


async def queue_in_project(
    db: AsyncSession, project_id: uuid.UUID, queue_id: uuid.UUID
) -> AnnotationQueue | None:
    return await db.scalar(
        select(AnnotationQueue).where(
            AnnotationQueue.project_id == project_id, AnnotationQueue.id == queue_id
        )
    )


async def upsert_policy(
    db: AsyncSession,
    project_id: uuid.UUID,
    values: dict[str, Any],
    *,
    actor_id: uuid.UUID,
    now: datetime,
) -> tuple[ReviewPolicy, bool]:
    """Create the project's policy or replace its settings; returns `(policy, created)`."""
    statement = insert(ReviewPolicy).values(
        id=new_id(),
        project_id=project_id,
        created_by=actor_id,
        created_at=now,
        updated_at=now,
        **values,
    )
    upsert = (
        statement.on_conflict_do_update(
            index_elements=[ReviewPolicy.project_id], set_={**values, "updated_at": now}
        )
        # xmax is 0 on a row this statement inserted and set on one it updated.
        .returning(ReviewPolicy.id, literal_column("(xmax = 0)", Boolean).label("created"))
    )
    policy_id, created = (await db.execute(upsert)).one()
    policy = await db.scalar(
        select(ReviewPolicy)
        .where(ReviewPolicy.id == policy_id)
        .execution_options(populate_existing=True)
    )
    assert policy is not None  # noqa: S101 - written by this transaction
    return policy, bool(created)


async def delete_policy(db: AsyncSession, project_id: uuid.UUID) -> bool:
    result = await db.execute(delete(ReviewPolicy).where(ReviewPolicy.project_id == project_id))
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def latest_selection(db: AsyncSession, project_id: uuid.UUID) -> ReviewSelection | None:
    return await db.scalar(
        select(ReviewSelection)
        .where(ReviewSelection.project_id == project_id)
        .order_by(ReviewSelection.day.desc())
        .limit(1)
    )


async def selection_for_day(
    db: AsyncSession, project_id: uuid.UUID, day: date
) -> ReviewSelection | None:
    return await db.scalar(
        select(ReviewSelection).where(
            ReviewSelection.project_id == project_id, ReviewSelection.day == day
        )
    )


async def list_selections(
    db: AsyncSession, project_id: uuid.UUID, *, before: date | None, limit: int
) -> list[ReviewSelection]:
    """Newest day first, up to `limit + 1` rows."""
    query = (
        select(ReviewSelection)
        .where(ReviewSelection.project_id == project_id)
        .order_by(ReviewSelection.day.desc())
        .limit(limit + 1)
    )
    if before is not None:
        query = query.where(ReviewSelection.day < before)
    return list((await db.scalars(query)).all())


async def job_id_for(db: AsyncSession, dedupe_key: str) -> int:
    """The id of the job with this dedupe key; the key is unique and was just enqueued."""
    return (await db.execute(select(Job.id).where(Job.dedupe_key == dedupe_key))).scalar_one()

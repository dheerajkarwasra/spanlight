"""Evaluator reads, and the rows an evaluator config names.

Evaluators and their runs are read in a transaction bound to the project, so row-level security
filters them; the `project_id` and `evaluator_id` conditions are the second line. Alert
channels belong to the organization and are read by `org_id`.
"""

import uuid
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AlertChannel, Evaluator, EvaluatorRun


async def list_evaluators(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    after: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> list[Evaluator]:
    """Newest first, up to `limit + 1` rows so the caller sees whether another page follows."""
    query = (
        select(Evaluator)
        .where(Evaluator.project_id == project_id)
        .order_by(Evaluator.created_at.desc(), Evaluator.id.desc())
        .limit(limit + 1)
    )
    if after is not None:
        moment, last_id = after
        query = query.where(
            or_(
                Evaluator.created_at < moment,
                and_(Evaluator.created_at == moment, Evaluator.id < last_id),
            )
        )
    return list(await db.scalars(query))


async def get_evaluator(
    db: AsyncSession, project_id: uuid.UUID, evaluator_id: uuid.UUID, *, lock: bool = False
) -> Evaluator | None:
    query = select(Evaluator).where(
        Evaluator.project_id == project_id, Evaluator.id == evaluator_id
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return await db.scalar(query)


async def list_runs(
    db: AsyncSession,
    evaluator_id: uuid.UUID,
    *,
    status: str | None,
    after: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> list[EvaluatorRun]:
    """The evaluator's runs, newest first, up to `limit + 1` rows; `status` filters."""
    query = (
        select(EvaluatorRun)
        .where(EvaluatorRun.evaluator_id == evaluator_id)
        .order_by(EvaluatorRun.started_at.desc(), EvaluatorRun.id.desc())
        .limit(limit + 1)
    )
    if status is not None:
        query = query.where(EvaluatorRun.status == status)
    if after is not None:
        moment, last_id = after
        query = query.where(
            or_(
                EvaluatorRun.started_at < moment,
                and_(EvaluatorRun.started_at == moment, EvaluatorRun.id < last_id),
            )
        )
    return list(await db.scalars(query))


async def org_channel(
    db: AsyncSession, org_id: uuid.UUID, channel_id: uuid.UUID
) -> AlertChannel | None:
    return await db.scalar(
        select(AlertChannel).where(AlertChannel.org_id == org_id, AlertChannel.id == channel_id)
    )

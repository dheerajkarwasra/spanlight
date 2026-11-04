"""Project-bound daily transitions; caller owns source locks, fence and commit."""

import uuid
from datetime import date, datetime

import structlog
from sqlalchemy import literal, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.attribution.daily_periods import (
    DAILY_KIND,
    DAY,
    DAY_JOB,
    EXPIRED,
    DailyPayload,
    latest_expired_period,
)
from app.db.models.aggregate_job_progress import AggregateJobProgress
from app.jobs.queue import enqueue, job_may_still_run

logger = structlog.get_logger(__name__)


async def locked_progress(db: AsyncSession, project: uuid.UUID) -> AggregateJobProgress:
    row = await db.scalar(
        select(AggregateJobProgress)
        .where(AggregateJobProgress.project_id == project, AggregateJobProgress.kind == DAILY_KIND)
        .with_for_update()
    )
    if row is None:
        raise RuntimeError("Surviving project is missing immutable daily admission")
    return row


async def retention_cutoff(db: AsyncSession, project: uuid.UUID) -> datetime:
    cutoff = await db.scalar(
        text("""
        SELECT clock_timestamp() - least(retention_days, 90) * interval '1 day'
        FROM projects WHERE id = :project
    """),
        {"project": project},
    )
    if not isinstance(cutoff, datetime):
        raise RuntimeError("Locked project disappeared")
    return cutoff


def expire_idle(row: AggregateJobProgress, cutoff: datetime, horizon: date) -> None:
    if row.active_period is not None:
        return
    if row.settled_period is None:
        raise RuntimeError("Daily admission is missing its settled sentinel")
    expired = latest_expired_period(cutoff, horizon)
    if expired <= row.settled_period:
        return
    logger.info(
        "attribution_daily_expiration_staged",
        first_period=row.settled_period + DAY,
        last_period=expired,
        outcome="expired",
    )
    row.settled_period = expired
    row.expired_through_period = expired
    row.settled_outcomes = EXPIRED


async def dispatch_day(db: AsyncSession, row: AggregateJobProgress, horizon: date) -> bool:
    if row.settled_period is None:
        raise RuntimeError("Daily admission is missing its settled sentinel")
    period = row.active_period or row.settled_period + DAY
    if period > horizon:
        return False
    if row.queued_key and await db.scalar(select(job_may_still_run(literal(row.queued_key)))):
        return False
    day = row.next_day or period - 7 * DAY
    number = row.dispatch_number + 1
    payload = DailyPayload(row.project_id, period, period - 7 * DAY, period - 2 * DAY, day, number)
    key = f"attribution-daily:{row.project_id}:{period}:{day}:{number}"
    if not await enqueue(db, DAY_JOB, payload.serialize(), dedupe_key=key, max_attempts=1):
        raise RuntimeError("Fresh daily dispatch key already exists")
    row.active_period, row.next_day = period, day
    row.dispatch_number, row.queued_key = number, key
    return True


def matches(row: AggregateJobProgress, payload: DailyPayload) -> bool:
    return (
        row.active_period == payload.scheduled_period
        and row.next_day == payload.day
        and row.dispatch_number == payload.dispatch_number
    )


def settle_day(row: AggregateJobProgress, payload: DailyPayload, outcome: int) -> None:
    row.active_outcomes |= outcome
    row.queued_key = None
    if payload.day < payload.last_day:
        row.next_day = payload.day + DAY
        return
    row.settled_period = payload.scheduled_period
    if row.active_outcomes == EXPIRED:
        row.expired_through_period = payload.scheduled_period
    row.settled_outcomes = row.active_outcomes
    row.active_period = row.next_day = None
    row.active_outcomes = 0

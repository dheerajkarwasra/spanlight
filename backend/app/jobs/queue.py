"""A lease-based job queue in Postgres.

Claiming a job takes a time-limited lease and increments its `fence`. Every
later state change (complete, fail, extend) is conditional on the fence the
worker received, so a worker whose lease expired and whose job was re-claimed
by another worker can no longer change it: its writes match zero rows.

`FOR UPDATE SKIP LOCKED` lets many workers poll concurrently without blocking
on each other's candidate rows.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import ColumnElement, exists, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, JobStatus
from app.jobs.outcome import JobOutcome

DEFAULT_LEASE = timedelta(seconds=60)
MAX_BACKOFF = timedelta(hours=1)
BASE_BACKOFF = timedelta(seconds=15)
# Room for a 2 KB stderr tail (the backup job) plus the exception type and a short prefix.
MAX_ERROR_LENGTH = 4000


class LeaseLostError(Exception):
    """The job was re-claimed by another worker after this worker's lease expired."""


@dataclass(frozen=True)
class ClaimedJob:
    id: int
    kind: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    fence: int


async def enqueue(
    db: AsyncSession,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    run_after: datetime | None = None,
    dedupe_key: str | None = None,
    max_attempts: int = 5,
) -> bool:
    """Queue a job. With a `dedupe_key`, a second enqueue is a no-op.

    Returns whether a new job was created. The caller commits.
    """
    values: dict[str, Any] = {
        "kind": kind,
        "payload": payload or {},
        "dedupe_key": dedupe_key,
        "max_attempts": max_attempts,
    }
    if run_after is not None:
        values["run_after"] = run_after
    statement = (
        insert(Job)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[Job.dedupe_key])
        .returning(Job.id)
    )
    return (await db.execute(statement)).scalar_one_or_none() is not None


def job_may_still_run(dedupe_key: ColumnElement[str]) -> ColumnElement[bool]:
    """A SQL condition: the job with this dedupe key is queued or running, so it may still run.
    For a caller that must tell work whose job died (one attempt, worker gone) from work that is
    only waiting."""
    return exists().where(
        Job.dedupe_key == dedupe_key,
        Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
    )


_EXPIRE_EXHAUSTED = text(
    """
    UPDATE jobs
    SET status = 'failed', lease_until = NULL,
        last_error = coalesce(last_error, 'lease expired after the final attempt')
    WHERE status = 'running' AND lease_until < now() AND attempts >= max_attempts
    """
)

_CLAIM = text(
    """
    UPDATE jobs
    SET status      = 'running',
        attempts    = attempts + 1,
        fence       = fence + 1,
        lease_until = now() + CAST(:lease AS interval)
    WHERE id = (
        SELECT id
        FROM jobs
        WHERE ((status = 'queued' AND run_after <= now())
           OR (status = 'running' AND lease_until < now() AND attempts < max_attempts))
          AND kind <> ALL (CAST(:excluded_kinds AS text[]))
        ORDER BY run_after, id
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    )
    RETURNING id, kind, payload, attempts, max_attempts, fence
    """
)


async def claim_next(
    db: AsyncSession,
    *,
    lease: timedelta = DEFAULT_LEASE,
    exclude_kinds: Collection[str] = (),
) -> ClaimedJob | None:
    """Claim the next runnable job (or one whose lease expired). Commits.

    Jobs of the kinds in `exclude_kinds` are left for later (or for another worker).
    """
    await db.execute(_EXPIRE_EXHAUSTED)
    row = (
        await db.execute(
            _CLAIM, {"lease": _interval(lease), "excluded_kinds": sorted(exclude_kinds)}
        )
    ).one_or_none()
    await db.commit()
    if row is None:
        return None
    return ClaimedJob(
        id=row.id,
        kind=row.kind,
        payload=row.payload,
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        fence=row.fence,
    )


_COMPLETE = text(
    """
    UPDATE jobs SET status = 'done', lease_until = NULL, last_error = NULL, outcome = :outcome
    WHERE id = :id AND fence = :fence AND status = 'running'
    """
)

_FAIL = text(
    """
    UPDATE jobs
    SET status      = CAST(:status AS job_status),
        run_after   = now() + CAST(:backoff AS interval),
        lease_until = NULL,
        last_error  = :error
    WHERE id = :id AND fence = :fence AND status = 'running'
    """
)

_EXTEND = text(
    """
    UPDATE jobs SET lease_until = now() + CAST(:lease AS interval)
    WHERE id = :id AND fence = :fence AND status = 'running'
    """
)


async def complete(db: AsyncSession, job: ClaimedJob, outcome: JobOutcome = JobOutcome.OK) -> None:
    """Mark the job done with `outcome`. A skipped outcome is final: `done` is never retried.

    Raises LeaseLostError if the fence no longer matches. Commits.
    """
    await _fenced_update(
        db, _COMPLETE, {"id": job.id, "fence": job.fence, "outcome": outcome.value}
    )


async def fail(
    db: AsyncSession, job: ClaimedJob, error: str, *, backoff: timedelta | None = None
) -> JobStatus:
    """Record a failure: retry later, or give up. Commits.

    The retry waits `backoff` when the task chose one (`RetryAfter`), else `retry_backoff`.
    """
    exhausted = job.attempts >= job.max_attempts
    status = JobStatus.FAILED if exhausted else JobStatus.QUEUED
    wait = backoff if backoff is not None else retry_backoff(job.attempts)
    params = {
        "id": job.id,
        "fence": job.fence,
        "status": status.value,
        "backoff": _interval(wait),
        "error": error[:MAX_ERROR_LENGTH],
    }
    await _fenced_update(db, _FAIL, params)
    return status


async def extend_lease(db: AsyncSession, job: ClaimedJob, lease: timedelta = DEFAULT_LEASE) -> None:
    """Heartbeat for long tasks. Raises LeaseLostError if the job was taken over. Commits."""
    await _fenced_update(db, _EXTEND, {"id": job.id, "fence": job.fence, "lease": _interval(lease)})


async def _fenced_update(db: AsyncSession, statement: Any, params: dict[str, Any]) -> None:
    result = await db.execute(statement, params)
    await db.commit()
    if getattr(result, "rowcount", 0) != 1:
        raise LeaseLostError(f"job {params['id']} is no longer held at fence {params['fence']}")


def retry_backoff(attempts: int) -> timedelta:
    """15s, 30s, 60s, … capped at one hour."""
    doublings = max(attempts - 1, 0)
    return min(BASE_BACKOFF * (1 << min(doublings, 16)), MAX_BACKOFF)


def _interval(delta: timedelta) -> str:
    return f"{delta.total_seconds()} seconds"

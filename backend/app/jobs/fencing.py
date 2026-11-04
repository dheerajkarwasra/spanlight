"""Commit-time domain fencing in the caller's transaction."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.queue import ClaimedJob, LeaseLostError


async def lock_live_job(db: AsyncSession, job: ClaimedJob) -> None:
    """Lock identity, then validate fresh DB wall time; caller commits immediately.

    The lock is last in domain lock order and survives through commit. Never
    heartbeat or perform domain work after this check; this helper never commits.
    """
    params = {"id": job.id, "kind": job.kind, "fence": job.fence}
    found = await db.scalar(text("SELECT id FROM jobs WHERE id = :id FOR UPDATE"), params)
    if found is None or not await db.scalar(
        text("""
            SELECT EXISTS (SELECT 1 FROM jobs WHERE id = :id AND kind = :kind
                AND fence = :fence AND status = 'running'
                AND lease_until IS NOT NULL AND lease_until > clock_timestamp())
        """),
        params,
    ):
        raise LeaseLostError(f"Job {job.id} lost its live commit lease")

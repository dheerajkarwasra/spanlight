"""The steps of one source's fetch that surround the provider call (spec §10.2 steps 1, 2, 5).

`open_attempt` loads the source and opens (or adopts) the run the attempt works under, or says
how the attempt ends instead; `open_key` opens the admin key in memory for this run only;
`refresh_rollups` recomputes the cost rollups of the source's projects over the window once the
lines are stored; `skip` counts and logs a fetch that did not happen. Log lines name the source
and the run by id, never the key.
"""

import time
import uuid
from datetime import timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.crypto import CryptoNotConfigured, DecryptionFailed, Sealed, UnknownKeyId, decrypt
from app.core.observability import BILLING_FETCH_RUNS
from app.db.models.billing import BillingFetchRun, BillingSource
from app.jobs.context import TaskContext
from app.jobs.outcome import JobOutcome, RetryAfter
from app.jobs.project_pass import AttemptMemory, run_project_pass
from app.reconciliation.fetch_policy import ChildRequest
from app.reconciliation.fetchers import FetchFailed
from app.reconciliation.job_queries import source_for_job
from app.reconciliation.queries import projects_of_source
from app.reconciliation.rollups import refresh_cost_rollups
from app.reconciliation.runs import (
    RUN_IN_PROGRESS_WINDOW,
    SOURCE_DISABLED_ERROR,
    RunInProgress,
    SourceGone,
    adopt_run,
    finish_run_failed,
    open_run,
)
from app.reconciliation.source_rules import is_api_kind

logger = structlog.get_logger(__name__)

# The worker cancels a task after 50 s: the rollups after a fetch start by 40 s after the job
# started, and none runs past 45 s.
ROLLUP_BUDGET_S = 40.0
ROLLUP_HARD_STOP_S = 45.0


async def open_attempt(
    context: TaskContext, request: ChildRequest
) -> tuple[BillingSource, BillingFetchRun] | JobOutcome | RetryAfter:
    """The source and the run this attempt works under, or how the attempt ends instead."""
    async with context.session_factory() as db:
        source = await source_for_job(db, request.source_id)
        if source is None or not source.enabled or not is_api_kind(source.kind):
            reason = "source_deleted" if source is None else "source_disabled_or_csv"
            skip(source.provider if source else None, request.source_id, reason)
            if source is not None and request.run_id is not None:
                await _close_manual_run(db, source, request.run_id)
            return JobOutcome.OK
        run = await adopt_run(db, source, request.run_id) if request.run_id else None
        try:
            run = run or await open_run(
                db,
                source,
                trigger="manual" if request.trigger == "manual" else "scheduled",
                day_from=request.day_from,
                day_to=request.day_to,
                requested_by=request.requested_by,
            )
        except RunInProgress:
            if context.job.attempts > 1:
                # Likely the run this job's crashed attempt left behind: wait until it is stale.
                return RetryAfter(RUN_IN_PROGRESS_WINDOW.total_seconds(), "fetch in progress")
            skip(source.provider, request.source_id, "RunInProgress")
            return JobOutcome.OK
        except SourceGone:
            skip(source.provider, request.source_id, "SourceGone")
            return JobOutcome.OK
        await db.commit()
    return source, run


async def _close_manual_run(db: AsyncSession, source: BillingSource, run_id: uuid.UUID) -> None:
    """Close the run a manual refresh opened for a source disabled since, so the source does not
    look busy (and refuse the next refresh) until the run goes stale."""
    run = await adopt_run(db, source, run_id)
    if run is not None:
        await finish_run_failed(db, run, error=SOURCE_DISABLED_ERROR, source_status=None)
        await db.commit()


def open_key(settings: Settings, source: BillingSource) -> str:
    """The clear admin key, for this run only; `FetchFailed` with the ADR 0009 wording."""
    if source.ciphertext is None or source.key_id is None:
        raise FetchFailed("key cannot be decrypted", retry="none")
    sealed = Sealed(ciphertext=source.ciphertext, key_id=source.key_id)
    try:
        return decrypt(sealed, settings=settings).decode()
    except CryptoNotConfigured:
        raise FetchFailed("CREDENTIALS_KEYS is not set", retry="none") from None
    except (UnknownKeyId, DecryptionFailed, UnicodeDecodeError):
        raise FetchFailed("key cannot be decrypted", retry="none") from None


async def refresh_rollups(
    context: TaskContext, source: BillingSource, request: ChildRequest, started: float
) -> None:
    """Recompute the cost rollups of the source's projects over the window, each bound to its
    project, within what is left of the job's time."""
    count = (request.day_to - request.day_from).days + 1
    days = [request.day_from + timedelta(days=offset) for offset in range(count)]
    elapsed = time.monotonic() - started

    async def list_projects(db: AsyncSession) -> list[uuid.UUID]:
        return await projects_of_source(db, source.org_id, source.id)

    async def refresh(db: AsyncSession, project_id: uuid.UUID) -> int:
        return await refresh_cost_rollups(db, project_id, days)

    try:
        await run_project_pass(
            context.session_factory,
            list_projects=list_projects,
            run_project=refresh,
            memory=AttemptMemory(),
            event="billing_fetch_rollups",
            budget_seconds=max(ROLLUP_BUDGET_S - elapsed, 0.0),
            hard_stop_seconds=max(ROLLUP_HARD_STOP_S - elapsed, 0.0),
        )
    except Exception as exc:  # noqa: BLE001 - the fetch is stored; the rollup passes catch up
        logger.error(
            "billing_fetch_rollups_failed", source_id=str(source.id), error_type=type(exc).__name__
        )


def skip(provider: str | None, source_id: uuid.UUID, reason: str) -> None:
    BILLING_FETCH_RUNS.labels(provider=provider or "unknown", outcome="skipped").inc()
    logger.info("billing_fetch_skipped", source_id=str(source_id), reason=reason)

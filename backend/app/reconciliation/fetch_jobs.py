"""The billing fetch jobs: the daily parent and one source's fetch (spec §10.1, §10.2).

`fetch_billing_sources` runs once a UTC day: it closes every run left `running` past the
15-minute window as failed (`timeout`), whatever its source (one disabled after its worker
crashed would otherwise show `running` for good), then enqueues one `fetch_billing_source` child
per enabled API source, deduplicated per source and day. The child (also what a manual refresh
enqueues) opens a run, opens the admin key in memory for this run only, fetches the window,
then writes the lines, the run and the source in one transaction, and refreshes the cost
rollups of the source's projects over the window. A failed fetch writes nothing but the failed
run. Retries follow `retry_for`: never for a refused key or an invalid answer, after the
provider's `Retry-After` (at most 15 minutes) when rate limited, after 5 then 30 minutes on a
provider or network error; the attempt that gives up marks the source `failing`.

The fetch runs under `FETCH_DEADLINE_S` so the job ends inside the worker's task timeout, and
it heartbeats before the provider is called. Log lines name the source and the run by id and
carry status lines only: never the key, never a response body.
"""

import asyncio
import time
from datetime import UTC
from typing import Any

import structlog
from sqlalchemy.exc import DataError, IntegrityError

from app.config import Settings
from app.core.observability import (
    BILLING_FETCH_DURATION,
    BILLING_FETCH_RUNS,
    BILLING_LINES_UPSERTED,
)
from app.db.models.billing import BillingFetchRun, BillingSource
from app.gateway.http import build_http_client
from app.jobs.context import TaskContext
from app.jobs.outcome import JobOutcome, RetryAfter
from app.jobs.queue import enqueue
from app.reconciliation.fetch_policy import (
    MAX_ATTEMPTS,
    ChildRequest,
    fetch_window,
    outcome_label,
    parse_child_payload,
    retry_for,
)
from app.reconciliation.fetch_steps import open_attempt, open_key, refresh_rollups, skip
from app.reconciliation.fetchers import FetchFailed, FetchResult, fetcher_for
from app.reconciliation.job_queries import close_stale_runs, enabled_api_sources
from app.reconciliation.queries import mapping_patterns
from app.reconciliation.runs import (
    FETCH_SOURCE_JOB,
    RunClosed,
    SourceGone,
    child_payload,
    finish_run_failed,
    finish_run_ok,
    write_lines,
)

logger = structlog.get_logger(__name__)

# The worker cancels a task after 50 s. Every page of the fetch is read by 35 s after the job
# started; the rollups that follow keep to `fetch_steps.ROLLUP_*`.
FETCH_DEADLINE_S = 35.0


async def run_fetch_billing_sources(context: TaskContext, _: dict[str, Any]) -> JobOutcome:
    """Enqueue one child per enabled API source, once per source and UTC day."""
    if not context.settings.billing_sources_enabled:
        return JobOutcome.SKIPPED_NOT_CONFIGURED
    now = context.clock()
    period = now.astimezone(UTC).date().isoformat()
    created = 0
    async with context.session_factory() as db:
        closed = await close_stale_runs(db)
        await db.commit()
        if closed:
            logger.warning("billing_fetch_stale_runs_closed", runs=closed)
        sources = await enabled_api_sources(db)
        for source_id, last_success_at in sources:
            day_from, day_to = fetch_window(now, first_fetch=last_success_at is None)
            created += await enqueue(
                db,
                FETCH_SOURCE_JOB,
                child_payload(source_id, day_from, day_to, trigger="scheduled"),
                dedupe_key=f"{FETCH_SOURCE_JOB}:{source_id}:{period}",
                max_attempts=MAX_ATTEMPTS,
            )
        await db.commit()
    logger.info("billing_fetch_children_enqueued", sources=len(sources), created=created)
    return JobOutcome.OK


async def run_fetch_billing_source(
    context: TaskContext, payload: dict[str, Any]
) -> JobOutcome | RetryAfter:
    """Fetch one source's window and store it (spec §10.2 steps 1-7)."""
    started = time.monotonic()
    try:
        request = parse_child_payload(payload)
    except ValueError:
        # Retrying cannot fix it. The field names only: the values are not worth logging.
        logger.error("billing_fetch_bad_payload", fields=sorted(str(name) for name in payload))
        return JobOutcome.OK
    if not context.settings.billing_sources_enabled:
        skip(None, request.source_id, "billing_sources_disabled")
        return JobOutcome.SKIPPED_NOT_CONFIGURED
    await context.heartbeat()
    opened = await open_attempt(context, request)
    if not isinstance(opened, tuple):
        return opened
    source, run = opened
    try:
        outcome = await _fetch_and_store(context, request, source, run, started)
    except (Exception, asyncio.CancelledError):
        await _close_unfinished(context, source, run)
        raise
    if outcome is None:  # stored: the run is `ok` and stays so whatever happens next
        await refresh_rollups(context, source, request, started)
        return JobOutcome.OK
    return outcome


async def _close_unfinished(
    context: TaskContext, source: BillingSource, run: BillingFetchRun
) -> None:
    """After an unexpected error (a bug, a lost database), close the run so it does not look in
    progress. A failure here is logged, never raised: the first error is the one to report."""
    try:
        async with context.session_factory() as db:
            await finish_run_failed(db, run, error=None, source_status=None)
            await db.commit()
    except Exception as exc:  # noqa: BLE001 - the caller re-raises the original error
        logger.error(
            "billing_fetch_run_close_failed",
            source_id=str(source.id),
            run_id=str(run.id),
            error_type=type(exc).__name__,
        )


async def _fetch_and_store(
    context: TaskContext,
    request: ChildRequest,
    source: BillingSource,
    run: BillingFetchRun,
    started: float,
) -> JobOutcome | RetryAfter | None:
    """Fetch and store the window; None once stored, else how the attempt ends."""
    try:
        key = open_key(context.settings, source)
    except FetchFailed as failure:
        await _failed(context, source, run, failure, final=True)
        not_set = failure.status_line == "CREDENTIALS_KEYS is not set"
        return JobOutcome.SKIPPED_NOT_CONFIGURED if not_set else JobOutcome.OK
    async with context.session_factory() as db:
        patterns = await mapping_patterns(db, source.org_id)
    try:
        deadline_s = FETCH_DEADLINE_S - (time.monotonic() - started)
        result = await _fetch(context.settings, source, key, request, patterns, deadline_s)
        await _store(context, run, result)
    except FetchFailed as failure:
        return await _failed_attempt(context, source, run, failure)
    except SourceGone:
        logger.warning("billing_fetch_source_gone", source_id=str(source.id), run_id=str(run.id))
        BILLING_FETCH_RUNS.labels(provider=source.provider, outcome="skipped").inc()
        return JobOutcome.OK
    except RunClosed:
        # Another fetch took this run for dead and closed it: its lines were not committed.
        logger.warning("billing_fetch_run_closed", source_id=str(source.id), run_id=str(run.id))
        BILLING_FETCH_RUNS.labels(provider=source.provider, outcome="skipped").inc()
        return JobOutcome.OK
    BILLING_FETCH_RUNS.labels(provider=source.provider, outcome="ok").inc()
    BILLING_LINES_UPSERTED.labels(provider=source.provider).inc(len(result.lines))
    logger.info(
        "billing_fetch_done",
        source_id=str(source.id),
        run_id=str(run.id),
        lines=len(result.lines),
        pages=result.pages,
    )
    return None


async def _fetch(
    settings: Settings,
    source: BillingSource,
    key: str,
    request: ChildRequest,
    patterns: list[str],
    deadline_s: float,
) -> FetchResult:
    """The fetcher's result, all pages read within `deadline_s` (what is left of the job's)."""
    fetcher = fetcher_for(source.kind)
    started = time.monotonic()
    try:
        if deadline_s <= 0:
            raise TimeoutError
        async with asyncio.timeout(deadline_s), build_http_client(settings) as http:
            return await fetcher.fetch(
                http, key, request.day_from, request.day_to, source.provider_scope, patterns
            )
    except TimeoutError:
        raise FetchFailed("timeout", retry="transient") from None
    finally:
        BILLING_FETCH_DURATION.labels(provider=source.provider).observe(time.monotonic() - started)


async def _store(context: TaskContext, run: BillingFetchRun, result: FetchResult) -> None:
    """Lines, run and source in one transaction; `SourceGone` when the source was deleted,
    `RunClosed` when the run was closed meanwhile. A value a column refuses (`DataError`) is an
    invalid response: the database's message quotes the statement, never fit for `last_error`."""
    duplicates = len(result.lines) - len({(line.day, line.line_key) for line in result.lines})
    if duplicates:
        logger.warning(
            "billing_fetch_duplicate_lines",
            source_id=str(run.source_id),
            run_id=str(run.id),
            duplicates=duplicates,
        )
        raise FetchFailed("invalid response", retry="none")
    async with context.session_factory() as db:
        try:
            written = await write_lines(db, run, result.lines)
            await finish_run_ok(db, run, lines_upserted=written, pages=result.pages)
            await db.commit()
        except IntegrityError as error:
            if getattr(error.orig, "sqlstate", None) == "23503":  # foreign_key_violation
                raise SourceGone from None
            raise
        except DataError:
            logger.warning(
                "billing_fetch_value_out_of_range", source_id=str(run.source_id), run_id=str(run.id)
            )
            raise FetchFailed("invalid response", retry="none") from None


async def _failed_attempt(
    context: TaskContext, source: BillingSource, run: BillingFetchRun, failure: FetchFailed
) -> JobOutcome | RetryAfter:
    """Record a failed attempt; retry after the policy's delay unless this was the last one."""
    attempt = context.job.attempts
    delay = retry_for(failure, attempt)
    final = delay is None or attempt >= context.job.max_attempts
    await _failed(context, source, run, failure, final=final)
    if final or delay is None:
        return JobOutcome.OK
    return RetryAfter(retry_after_s=delay.total_seconds(), reason=failure.status_line)


async def _failed(
    context: TaskContext,
    source: BillingSource,
    run: BillingFetchRun,
    failure: FetchFailed,
    *,
    final: bool,
) -> None:
    """Close the run as failed; the attempt that gives up also marks the source `failing`."""
    async with context.session_factory() as db:
        await finish_run_failed(
            db, run, error=failure.status_line, source_status="failing" if final else None
        )
        await db.commit()
    BILLING_FETCH_RUNS.labels(provider=source.provider, outcome=outcome_label(failure)).inc()
    logger.warning(
        "billing_fetch_failed",
        source_id=str(source.id),
        run_id=str(run.id),
        status_line=failure.status_line,
        attempt=context.job.attempts,
        final=final,
    )

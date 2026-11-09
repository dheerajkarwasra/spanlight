"""The worker process: `python -m app.jobs.worker`.

Loop: schedule periodic jobs, claim one job, run it under a timeout shorter
than the lease, then complete or fail it (fenced). Stops on SIGINT/SIGTERM
after the current job finishes.

Jobs of the kinds in `TASK_TIMEOUT_OVERRIDES` (a database backup, an export) can run for hours.
They run in a slot of their own, one at a time, as a separate task, so the loop keeps claiming
the short kinds (notifications, rollups, retention) meanwhile. A long job keeps its own lease
alive; on shutdown it is cancelled and put back in the queue.

Maintenance (`MAINTENANCE_KINDS`: a backup, an end-user erasure) has a second, reserved long slot,
so a due backup or erasure is not held behind an experiment that runs for hours; experiments and
exports never use it. The queue gauges (`queue_gauges`) say what still waits.
"""

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.core.observability import JOBS_FINISHED
from app.jobs import queue
from app.jobs.context import TaskContext, TaskHandler
from app.jobs.metrics_server import serve_metrics
from app.jobs.outcome import JobOutcome, RetryAfter
from app.jobs.queue_gauges import sample_queue_gauges
from app.jobs.scheduler import schedule_periodic
from app.jobs.tasks import TASKS
from app.jobs.worker_heartbeat import new_worker_id, run_heartbeat
from app.jobs.worker_lifecycle import run_worker
from app.jobs.worker_operations import Operation, worker_operation
from app.jobs.worker_progress import ShortLoopProgress
from app.process_shutdown import budget_from_settings
from app.rollups.coverage_status import warn_if_coverage_inactive

logger = structlog.get_logger(__name__)

POLL_INTERVAL_SECONDS = 2.0
SCHEDULE_INTERVAL = timedelta(seconds=30)
# Leaves headroom inside the 60 s lease so a slow task cannot outlive it.
TASK_TIMEOUT_SECONDS = 50.0
# Kinds that legitimately run longer. Such a task must extend its own lease (`heartbeat`) while it
# works: the lease stays 60 s, so a worker that dies still frees the job within a minute.
TASK_TIMEOUT_OVERRIDES: Mapping[str, float] = {
    "backup_database": 3 * 60 * 60.0,
    # Streaming up to 100 000 traces with their spans to object storage; it extends its lease
    # after every batch.
    "create_export": 60 * 60.0,
    # An experiment run: up to 1 000 items, four provider calls in flight, each followed by its
    # evaluators; it extends its lease before every call and every 20 seconds.
    "run_experiment": 3 * 60 * 60.0,
    # Erasing one end user: batches of a few thousand spans until none is left, for a user who
    # may own millions; it extends its lease before every batch and every 20 seconds.
    "erase_end_user": 3 * 60 * 60.0,
}
# Kinds that run in a long-job slot.
LONG_KINDS: frozenset[str] = frozenset(TASK_TIMEOUT_OVERRIDES)
# Long kinds with their own reserved slot: a due one waits only for the previous maintenance
# job, never for an experiment or an export. Readiness Task 7D (300 s queue-wait target).
MAINTENANCE_KINDS: frozenset[str] = frozenset({"backup_database", "erase_end_user"})


class Worker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        handlers: Mapping[str, TaskHandler] = TASKS,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.handlers = handlers
        self._last_scheduled: datetime | None = None
        self._long_job: asyncio.Task[None] | None = None
        self._maintenance_job: asyncio.Task[None] | None = None
        self.worker_id = new_worker_id()
        self.progress = ShortLoopProgress()
        self._stop: asyncio.Event | None = None

    @contextlib.asynccontextmanager
    async def _operation(
        self, operation: Operation, *, short: bool = True
    ) -> AsyncIterator[AsyncSession]:
        if short:
            self.progress.enter(operation)
        try:
            async with worker_operation(self.session_factory, operation) as db:
                yield db
        finally:
            if short:
                self.progress.enter("idle")

    async def schedule_if_due(self) -> None:
        now = datetime.now(UTC)
        if self._last_scheduled and now - self._last_scheduled < SCHEDULE_INTERVAL:
            return
        async with self._operation("schedule") as db:
            created = await schedule_periodic(db, self.settings, now)
        if created:
            logger.info("periodic_jobs_enqueued", kinds=created)
        self._last_scheduled = now

    @staticmethod
    def _still_running(task: asyncio.Task[None] | None) -> asyncio.Task[None] | None:
        """The slot's task while it runs, else None. Logs a task that died with an error."""
        if task is None or not task.done():
            return task
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error("long_job_crashed", exc_info=error)
        return None

    async def run_once(self) -> bool:
        """Claim a job and run it. Returns whether a job was claimed.

        A short job runs to the end before this returns. A long job is started as a task in its
        slot and this returns at once; while a slot is taken no second job of its kinds is
        claimed: experiments and exports share one slot, backups and erasures another.
        """
        if self._stop is not None and self._stop.is_set():
            return False
        self._long_job = self._still_running(self._long_job)
        self._maintenance_job = self._still_running(self._maintenance_job)
        exclude: set[str] = set()
        if self._long_job is not None:
            exclude |= LONG_KINDS - MAINTENANCE_KINDS
        if self._maintenance_job is not None:
            exclude |= MAINTENANCE_KINDS
        async with self._operation("claim") as db:
            job = await queue.claim_next(
                db, exclude_kinds=exclude, supported_kinds=self.handlers.keys()
            )
        self.progress.poll_succeeded()
        if job is None:
            return False
        if self._stop is not None and self._stop.is_set():
            await self._record_failure(job, RuntimeError("worker shut down"), logger)
            return True
        if job.kind in MAINTENANCE_KINDS:
            self._maintenance_job = asyncio.create_task(
                self._execute(job), name=f"long-job-{job.kind}"
            )
            return True
        if job.kind in LONG_KINDS:
            self._long_job = asyncio.create_task(self._execute(job), name=f"long-job-{job.kind}")
            return True
        self.progress.enter("short_execution")
        try:
            await self._execute(job)
        finally:
            self.progress.enter("idle")
        return True

    async def _execute(self, job: queue.ClaimedJob) -> None:
        log = logger.bind(job_id=job.id, kind=job.kind, attempt=job.attempts)
        handler = self.handlers.get(job.kind)
        context = TaskContext(job=job, settings=self.settings, session_factory=self.session_factory)
        try:
            if handler is None:
                raise LookupError(f"no handler registered for job kind {job.kind!r}")
            timeout = TASK_TIMEOUT_OVERRIDES.get(job.kind, TASK_TIMEOUT_SECONDS)
            async with asyncio.timeout(timeout):
                outcome = await handler(context, job.payload) or JobOutcome.OK
        except queue.LeaseLostError:
            log.warning("job_lease_lost")
            JOBS_FINISHED.labels(job.kind, "lease_lost").inc()
            return
        except asyncio.CancelledError:
            # The worker is shutting down. Record the attempt as failed so the job goes back in
            # the queue after the usual retry backoff (15 s for a first attempt), instead of
            # waiting for the lease to run out. The attempt counts towards the job's limit.
            try:
                await self._record_failure(job, RuntimeError("worker shut down"), log)
            except Exception:
                log.exception("job_shutdown_persistence_uncertain")
            raise
        except Exception as exc:  # noqa: BLE001 - any task failure is recorded on the job
            await self._record_failure(job, exc, log)
            return

        if isinstance(outcome, RetryAfter):
            await self._record_retry(job, outcome, log)
            return
        try:
            async with self._operation("complete", short=job.kind not in LONG_KINDS) as db:
                await queue.complete(db, job, outcome)
        except queue.LeaseLostError:
            log.warning("job_lease_lost_on_complete")
            JOBS_FINISHED.labels(job.kind, "lease_lost").inc()
            return
        log.info("job_done", outcome=outcome.value)
        JOBS_FINISHED.labels(job.kind, "skipped" if outcome.is_skipped else "done").inc()

    async def _record_failure(
        self, job: queue.ClaimedJob, exc: Exception, log: structlog.stdlib.BoundLogger
    ) -> None:
        error = f"{type(exc).__name__}: {exc}"
        try:
            async with self._operation("fail", short=job.kind not in LONG_KINDS) as db:
                status = await queue.fail(db, job, error)
        except queue.LeaseLostError:
            log.warning("job_lease_lost_on_fail")
            return
        log.error("job_failed", error=error, final=status.value == "failed", exc_info=exc)
        JOBS_FINISHED.labels(job.kind, "failed" if status.value == "failed" else "retry").inc()

    async def _record_retry(
        self, job: queue.ClaimedJob, retry: RetryAfter, log: structlog.stdlib.BoundLogger
    ) -> None:
        """The task asked for another attempt after its own delay; the attempt counts."""
        try:
            async with self._operation("retry", short=job.kind not in LONG_KINDS) as db:
                status = await queue.fail(db, job, retry.reason, backoff=retry.delay)
        except queue.LeaseLostError:
            log.warning("job_lease_lost_on_retry")
            return
        final = status.value == "failed"
        log.warning(
            "job_retry_after", reason=retry.reason, retry_after_s=retry.retry_after_s, final=final
        )
        JOBS_FINISHED.labels(job.kind, "failed" if final else "retry").inc()

    async def _stop_long_job(self) -> None:
        """Cancel and join both long slots."""
        tasks = [task for task in (self._long_job, self._maintenance_job) if task is not None]
        for task in tasks:
            if not task.done():
                logger.info("long_job_cancelled_on_shutdown", task=task.get_name())
                task.cancel()
        for task in tasks:
            # An error the job's own handling did not consume must not escape: it would skip
            # cancelling the heartbeat in `run_forever`'s `finally`.
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("long_job_failed_on_shutdown", task=task.get_name())

    async def run_forever(self, stop: asyncio.Event) -> None:
        self._stop = stop
        crypto_configured = self.settings.is_crypto_configured
        logger.info(
            "worker_started",
            worker_id=self.worker_id,
            tasks=sorted(self.handlers),
            crypto_configured=crypto_configured,
        )
        if not crypto_configured:
            # Say it once at start-up, not only when the first run fails: the api can hold
            # CREDENTIALS_KEYS while the worker, a separate process, does not.
            logger.warning(
                "worker_credentials_keys_missing",
                impact="the worker cannot open stored secrets: experiment runs, LLM judge and "
                "webhook evaluators fail, Slack, webhook and PagerDuty alerts are not delivered "
                "and demo traffic is skipped; set CREDENTIALS_KEYS on the worker",
            )
        await warn_if_coverage_inactive(self.session_factory)
        # Its own task, so a job that runs for hours does not stop the heartbeat readiness reads.
        # The queue gauges ride on the same tick, with the registry frozen here.
        kinds = frozenset(self.handlers)
        heartbeat = asyncio.create_task(
            run_heartbeat(
                self.session_factory,
                stop,
                self.worker_id,
                report_progress=self.progress.report,
                sample_queue=partial(sample_queue_gauges, self.session_factory, kinds),
            ),
            name="worker-heartbeat",
        )
        try:
            while not stop.is_set():
                schedule_failed = False
                try:
                    await self.schedule_if_due()
                except Exception:
                    logger.exception("worker_schedule_failed")
                    schedule_failed = True
                if stop.is_set():
                    break
                try:
                    ran = await self.run_once()
                except Exception:
                    logger.exception("worker_iteration_failed")
                    ran = False
                if not ran or schedule_failed:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL_SECONDS)
        finally:
            await self._stop_long_job()
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            self._stop = None
        logger.info("worker_stopped")


async def main(*, owns_executor: bool = False) -> None:
    os.environ.pop("MIGRATION_DATABASE_URL", None)
    settings = get_settings()
    await run_worker(
        settings, budget_from_settings(settings), Worker, serve_metrics, owns_executor=owns_executor
    )


if __name__ == "__main__":
    asyncio.run(main(owns_executor=True))

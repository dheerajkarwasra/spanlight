"""The worker process: `python -m app.jobs.worker`.

Loop: schedule periodic jobs, claim one job, run it under a timeout shorter
than the lease, then complete or fail it (fenced). Stops on SIGINT/SIGTERM
after the current job finishes.

Jobs of the kinds in `TASK_TIMEOUT_OVERRIDES` (a database backup, an export) can run for hours.
They run in a slot of their own, one at a time, as a separate task, so the loop keeps claiming
the short kinds (notifications, rollups, retention) meanwhile. A long job keeps its own lease
alive; on shutdown it is cancelled and put back in the queue.
"""

import asyncio
import contextlib
import signal
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.channels import register_channel_deliverers
from app.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.observability import JOBS_FINISHED
from app.core.sentry import init_sentry
from app.core.tracing import configure_worker_tracing, shutdown_tracing
from app.db.session import create_engine, create_session_factory
from app.jobs import queue
from app.jobs.context import TaskContext, TaskHandler
from app.jobs.metrics_server import serve_metrics
from app.jobs.outcome import JobOutcome
from app.jobs.scheduler import schedule_periodic
from app.jobs.tasks import TASKS
from app.jobs.worker_heartbeat import new_worker_id, run_heartbeat
from app.notifications.defaults import register_default_deliverers
from app.pricing.cost import sync_seed_prices

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
}
# Kinds that run in the long-job slot.
LONG_KINDS: frozenset[str] = frozenset(TASK_TIMEOUT_OVERRIDES)


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
        self.worker_id = new_worker_id()

    async def schedule_if_due(self) -> None:
        now = datetime.now(UTC)
        if self._last_scheduled and now - self._last_scheduled < SCHEDULE_INTERVAL:
            return
        async with self.session_factory() as db:
            created = await schedule_periodic(db, self.settings, now)
        if created:
            logger.info("periodic_jobs_enqueued", kinds=created)
        self._last_scheduled = now

    def _long_job_running(self) -> bool:
        """Whether the long-job slot is taken. Also logs and frees a slot whose task died."""
        task = self._long_job
        if task is None:
            return False
        if not task.done():
            return True
        self._long_job = None
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error("long_job_crashed", exc_info=error)
        return False

    async def run_once(self) -> bool:
        """Claim a job and run it. Returns whether a job was claimed.

        A short job runs to the end before this returns. A long job is started as a task in the
        long-job slot and this returns at once; while the slot is taken no second long job is
        claimed.
        """
        exclude = LONG_KINDS if self._long_job_running() else ()
        async with self.session_factory() as db:
            job = await queue.claim_next(db, exclude_kinds=exclude)
        if job is None:
            return False
        if job.kind in LONG_KINDS:
            self._long_job = asyncio.create_task(self._execute(job), name=f"long-job-{job.kind}")
            return True
        await self._execute(job)
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
            await self._record_failure(job, RuntimeError("worker shut down"), log)
            raise
        except Exception as exc:  # noqa: BLE001 - any task failure is recorded on the job
            await self._record_failure(job, exc, log)
            return

        try:
            async with self.session_factory() as db:
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
            async with self.session_factory() as db:
                status = await queue.fail(db, job, error)
        except queue.LeaseLostError:
            log.warning("job_lease_lost_on_fail")
            return
        log.error("job_failed", error=error, final=status.value == "failed", exc_info=exc)
        JOBS_FINISHED.labels(job.kind, "failed" if status.value == "failed" else "retry").inc()

    async def _stop_long_job(self) -> None:
        task = self._long_job
        if task is not None and not task.done():
            logger.info("long_job_cancelled_on_shutdown", task=task.get_name())
            task.cancel()
        if task is not None:
            # An error the job's own handling did not consume must not escape: it would skip
            # cancelling the heartbeat in `run_forever`'s `finally`.
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("long_job_failed_on_shutdown", task=task.get_name())

    async def run_forever(self, stop: asyncio.Event) -> None:
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
        # Its own task, so a job that runs for hours does not stop the heartbeat readiness reads.
        heartbeat = asyncio.create_task(
            run_heartbeat(self.session_factory, stop, self.worker_id), name="worker-heartbeat"
        )
        try:
            while not stop.is_set():
                try:
                    await self.schedule_if_due()
                    ran = await self.run_once()
                except Exception:
                    logger.exception("worker_iteration_failed")
                    ran = False
                if not ran:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL_SECONDS)
        finally:
            await self._stop_long_job()
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        logger.info("worker_stopped")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    init_sentry(settings)
    # The worker delivers every queued notification (the api only delivers a channel's test-send).
    register_default_deliverers(settings)
    engine = create_engine(settings.database_url, pool_size=2)
    tracer_provider = configure_worker_tracing(settings, engine)
    session_factory = create_session_factory(engine)
    alert_http = register_channel_deliverers(session_factory, settings)

    async with session_factory() as db:
        await sync_seed_prices(db)
        await db.commit()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)

    try:
        async with serve_metrics(settings.worker_metrics_port, settings.metrics_token):
            await Worker(session_factory, settings).run_forever(stop)
    finally:
        await alert_http.aclose()
        await engine.dispose()
        await shutdown_tracing(tracer_provider)


if __name__ == "__main__":
    asyncio.run(main())

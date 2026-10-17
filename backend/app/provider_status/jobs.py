"""The `poll_provider_status` job: poll every status feed once, every 5 minutes.

Adapters run one after the other, each in its own `try` and its own session, so one feed's
failure (or a bug in one adapter) never stops the others, and the job always ends `done`: the
next run five minutes later is the retry. Each adapter gets one structured log line with the
outcome and the parse counts, never the body, and every request is counted in
`spanlight_provider_status_polls_total`. Two runs at once (rare: the scheduler enqueues one per
period) are harmless: the upsert is idempotent, and if the older of two fetches commits last,
the incidents and the source row show that slightly older state until the next poll.
"""

import time
from typing import Any

import httpx
import structlog

from app.core.observability import PROVIDER_STATUS_POLLS
from app.jobs.context import TaskContext
from app.jobs.outcome import JobOutcome
from app.provider_status.adapters import ADAPTERS, FeedAdapter
from app.provider_status.http import build_status_client
from app.provider_status.poller import PollOutcome, PollResult, poll_one

logger = structlog.get_logger(__name__)

_SUCCESS = frozenset({PollOutcome.FETCHED, PollOutcome.NOT_MODIFIED})


async def run_poll_provider_status(context: TaskContext, _: dict[str, Any]) -> JobOutcome | None:
    if not context.settings.provider_status_polling_enabled:
        # A run queued before polling was switched off: an air-gapped instance fetches nothing.
        return JobOutcome.SKIPPED_NOT_CONFIGURED
    async with build_status_client() as client:
        for adapter in ADAPTERS:
            await _poll(context, client, adapter)
    return None


async def _poll(context: TaskContext, client: httpx.AsyncClient, adapter: FeedAdapter) -> None:
    started = time.monotonic()
    try:
        async with context.session_factory() as db:
            result = await poll_one(client, db, adapter, now=context.clock())
    except Exception as error:  # noqa: BLE001 - one feed's failure must not stop the others
        # Only the class name: a database error's message can quote the incident rows.
        logger.error(
            "provider_status_poll_failed",
            provider=adapter.provider,
            error_type=type(error).__name__,
        )
        return
    for outcome in result.attempts:
        PROVIDER_STATUS_POLLS.labels(provider=adapter.provider, outcome=outcome.value).inc()
    _log(adapter.provider, result, duration_ms=round((time.monotonic() - started) * 1000))


def _log(provider: str, result: PollResult, *, duration_ms: int) -> None:
    report = result.report
    fields: dict[str, Any] = {
        "provider": provider,
        "outcome": result.outcome.value,
        "fell_back_from": result.fell_back_from.value if result.fell_back_from else None,
        "source": result.source,
        "status_code": result.status_code,
        "error_type": result.error_type,
        "kept": report.kept if report else None,
        "skipped_naive": report.skipped_naive if report else None,
        "skipped_url": report.skipped_url if report else None,
        "skipped_invalid": report.skipped_invalid if report else None,
        "upserted": result.upserted,
        "duration_ms": duration_ms,
    }
    if result.outcome is PollOutcome.BLOCKED:
        # The egress check refused a feed host: a configuration problem, not a flaky feed.
        logger.error("provider_status_polled", **fields)
    elif result.outcome in _SUCCESS and result.fell_back_from is None:
        logger.info("provider_status_polled", **fields)
    else:
        logger.warning("provider_status_polled", **fields)

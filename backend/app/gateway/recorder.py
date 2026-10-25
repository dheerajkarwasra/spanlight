"""Writing a gateway call's span, and what the call used, after the client has its answer.

`record` runs in a session of its own, so it never shares a transaction with the request (or
holds a lock the request needs); `app.gateway.span_write` says what one write stores.

A write that fails for a reason that may pass (a lost connection, a timeout, a deadlock: see
`app.core.db_errors.is_transient`) is retried after each of `RECORD_RETRY_DELAYS_S` (waiting
through the context's `sleep`); each try is one transaction, so a failed one leaves nothing
behind, and a retry first checks whether the span is stored already (a try whose commit went
through but was not acknowledged), so the key's minute counts the call once. Nothing is raised:
the client was already answered. A span still not stored after the last retry, or whose write
failed for a reason a retry would meet again (a deleted project, a value or constraint the
database refuses, a bug), is logged by the error's type and SQLSTATE, never its message, and
counted as `reason="error"`; a span ingestion refuses is not retried (it would be refused
again) and is counted as `reason="rejected"`.

`record_soon` runs `record` as a task the caller does not wait for, which also survives the
client going away mid-stream; `drain` waits for those tasks (shutdown, tests). A context with
a `span_sink` takes the span instead: its caller awaits each write itself (`DirectSpans`, the
demo job, Explain), retries included, so a failing database adds up to about 2.6 s to it.

The background writes are bounded, since each holds a database connection from the pool the
requests use: at most `GATEWAY_RECORD_CONCURRENCY` tries run at once (a write waiting to retry
holds no slot), and at most `GATEWAY_RECORD_BACKLOG` spans wait or run. A queued span's input
and output are already redacted and cut as ingestion stores them (`prepare_span_payloads`, at
most 1 MiB each), and the waiting spans' payloads together stay within
`GATEWAY_RECORD_BACKLOG_MB` (counted as their stored JSON bytes), so the backlog's memory is
bounded however large the requests. A span past either bound is dropped, counted as
`reason="overflow"` and logged at most once a minute, without its contents. A `low_priority`
span (a call the key limiter refused) is dropped the same way once half of either bound is taken
(at least one place), so a refusal flood leaves admitted calls the other half. A span whose
payloads cannot be prepared is counted as `reason="error"`.

Every span lost one of these ways is counted by `app.gateway.span_losses.lose`: in the metric by
reason, in `spans_lost()`, which `/health/ready` reports as `gateway_spans_lost` (the gateway
spans this process could not store since it started), and by project and hour for the
`telemetry_losses_hourly` flush.
"""

import asyncio
import contextlib
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.budgets.reserve import settle_unstored
from app.core.db_errors import is_transient, sqlstate
from app.core.observability import INGESTED_SPANS, REJECTED_SPANS
from app.gateway.context import GatewayContext, GatewayResult
from app.gateway.span_losses import lose, spans_lost
from app.gateway.span_write import write_span
from app.ingest.normalize import prepare_span_payloads
from app.ingest.pipeline import IngestOutcome, IngestTarget

logger = structlog.get_logger(__name__)

METRIC_SOURCE = "gateway"
OVERFLOW_LOG_INTERVAL_S = 60.0
# The waits before each retry of a failed span write: four tries over about 2.6 s. A constant
# beside the `GATEWAY_RECORD_*` settings (`app.config`), which bound how many writes run and wait.
RECORD_RETRY_DELAYS_S = (0.1, 0.5, 2.0)


@dataclass
class _Writers:
    """The background writes of one event loop, and how many may run and wait."""

    loop: asyncio.AbstractEventLoop
    slots: asyncio.Semaphore
    backlog: int
    backlog_bytes: int
    pending: set["asyncio.Task[None]"] = field(default_factory=set)
    held_bytes: int = 0  # the stored payload bytes of the pending spans
    dropped: int = 0  # since the last overflow log line
    logged_at: float | None = None


_writers: _Writers | None = None


async def record(
    context: GatewayContext, span: dict[str, Any], credential_ids: Sequence[uuid.UUID] = ()
) -> bool:
    """Write the span and the key's usage, retrying a failed write. True when the span was stored.

    Never raises (see the module docstring): a lost or refused span is logged, counted and
    answered with False.
    """
    return await _record(context, span, credential_ids, slots=None, prepared_truncated=None)


async def _record(
    context: GatewayContext,
    span: dict[str, Any],
    credential_ids: Sequence[uuid.UUID],
    *,
    slots: asyncio.Semaphore | None,
    prepared_truncated: bool | None,
) -> bool:
    """`record`, each try holding one of `slots` when given, for a span whose payloads may
    already be prepared (`prepared_truncated`, see `IngestTarget`)."""
    target = IngestTarget(
        project_id=context.project_id,
        capture_payloads=context.capture_payloads,
        org_id=context.org_id,
        source_key_id=context.key_id,
        prepared_truncated=prepared_truncated,
    )
    outcome = await _write_with_retries(context, target, span, credential_ids, slots)
    if outcome is None:
        return False
    INGESTED_SPANS.labels(METRIC_SOURCE).inc(outcome.accepted)
    if outcome.rejected:
        REJECTED_SPANS.labels(METRIC_SOURCE).inc(len(outcome.rejected))
        lose(context.project_id, "rejected")
        logger.warning(
            "gateway.span_rejected",
            span_id=span.get("span_id"),
            reason=outcome.rejected[0].reason,
        )
        return False
    return True


async def _write_with_retries(
    context: GatewayContext,
    target: IngestTarget,
    span: dict[str, Any],
    credential_ids: Sequence[uuid.UUID],
    slots: asyncio.Semaphore | None,
) -> IngestOutcome | None:
    """`write_span`, retried after each of `RECORD_RETRY_DELAYS_S`; None if the last try fails.

    Each try holds one of `slots` (when given), and none is held while waiting to retry. Only a
    transient failure (`is_transient`) is retried: any other would fail every retry too.
    """
    now = context.clock()  # every try counts the call in the same minute
    span_id = span.get("span_id")
    for tries, retry_in_s in enumerate((*RECORD_RETRY_DELAYS_S, None)):
        try:
            async with slots if slots is not None else contextlib.nullcontext():
                return await write_span(context, target, span, credential_ids, now, retry=tries > 0)
        except Exception as failure:  # noqa: BLE001 - counted and logged, the client was answered
            if retry_in_s is None or not is_transient(failure):
                _write_lost(context.project_id, span, failure, tries=tries + 1)
                await settle_unstored(context.sessions, context.project_id, span)
                return None
            logger.warning(
                "gateway.record_retry",
                span_id=span_id,
                error_type=type(failure).__name__,
                retry_in_s=retry_in_s,
            )
            await context.sleep(retry_in_s)
    return None  # not reached: the last try returns from the loop


def _write_lost(
    project_id: uuid.UUID, span: dict[str, Any], failure: BaseException, *, tries: int
) -> None:
    """Count and log a lost span by error type and SQLSTATE: the message may quote the payload."""
    lose(project_id, "error")
    logger.error(
        "gateway.record_failed",
        span_id=span.get("span_id"),
        trace_id=span.get("trace_id"),
        stage="write",
        error_type=type(failure).__name__,
        sqlstate=sqlstate(failure),
        tries=tries,
    )


def record_soon(
    context: GatewayContext,
    span: dict[str, Any],
    credential_ids: Sequence[uuid.UUID] = (),
    *,
    low_priority: bool = False,
) -> None:
    """Run `record` in the background, or drop the span when the backlog is full (half full for
    a `low_priority` span). A context with a `span_sink` hands the span to it instead.
    """
    if context.span_sink is not None:
        context.span_sink.add(span, credential_ids)
        return
    writers = _writers_for(context)
    limit = max(1, writers.backlog // 2) if low_priority else writers.backlog
    if len(writers.pending) >= limit:
        _overflow(writers, context.project_id)
        return
    # Held until written, so held as stored: redacted and at most 1 MiB per payload.
    try:
        span, truncated, size = prepare_span_payloads(
            span, capture_payloads=context.capture_payloads
        )
    except Exception as failure:  # noqa: BLE001 - counted and logged, the client was answered
        # Counted like a write that never succeeds. Only the type is logged: the message may
        # quote the payload.
        lose(context.project_id, "error")
        logger.error(
            "gateway.record_failed",
            span_id=span.get("span_id"),
            stage="prepare",
            error_type=type(failure).__name__,
        )
        _settle_lost(writers, context, span)
        return
    byte_limit = writers.backlog_bytes // 2 if low_priority else writers.backlog_bytes
    # The first waiting span is always taken, so one span larger than the budget is not lost.
    if writers.pending and writers.held_bytes + size > byte_limit:
        _overflow(writers, context.project_id)
        return
    task = writers.loop.create_task(_write(writers, context, span, credential_ids, truncated))
    writers.pending.add(task)
    writers.held_bytes += size

    def _release(done: "asyncio.Task[None]") -> None:
        writers.pending.discard(done)
        writers.held_bytes -= size

    task.add_done_callback(_release)


def credentials_used(result: GatewayResult) -> tuple[uuid.UUID, ...]:
    """The credentials the call's attempts sent a request with; a blocked attempt sent none."""
    used = (attempt.credential_id for attempt in result.attempts if attempt.error != "blocked")
    return tuple(dict.fromkeys(used))


class DirectSpans:
    """A `SpanSink` whose owner writes each span itself, awaiting the write (`write`)."""

    def __init__(self) -> None:
        self._pending: list[tuple[dict[str, Any], Sequence[uuid.UUID]]] = []

    def add(self, span: dict[str, Any], credential_ids: Sequence[uuid.UUID]) -> None:
        self._pending.append((span, credential_ids))

    async def write(self, context: GatewayContext, *, require_span: bool = False) -> bool:
        """Write the spans taken so far, one by one. False when any of them was not stored.

        With nothing taken, the answer is True, since a cancelled call may legitimately have
        produced no span. A caller that just made a call it will pay for passes `require_span`:
        a call that left no span behind (its span could not even be built) is spend it cannot
        account for, so no span waiting counts as not stored.
        """
        stored = not (require_span and not self._pending)
        while self._pending:
            span, credential_ids = self._pending.pop(0)
            stored = await record(context, span, credential_ids) and stored
        return stored


async def drain(timeout_s: float | None = None) -> None:
    """Wait for the spans still being written, at most `timeout_s` when given."""
    writers = _writers
    if writers is None or writers.loop is not asyncio.get_running_loop():
        return
    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    while writers.pending:
        left = None if deadline is None else deadline - time.monotonic()
        if left is not None and left <= 0:
            logger.warning("gateway.record_drain_timeout", pending=len(writers.pending))
            return
        await asyncio.wait(list(writers.pending), timeout=left)


async def _write(
    writers: _Writers,
    context: GatewayContext,
    span: dict[str, Any],
    credential_ids: Sequence[uuid.UUID],
    truncated: bool,
) -> None:
    await _record(context, span, credential_ids, slots=writers.slots, prepared_truncated=truncated)


def _writers_for(context: GatewayContext) -> _Writers:
    """This loop's writers, made on first use (a test may run several loops one after another)."""
    global _writers  # noqa: PLW0603 - one set of writers per process, rebuilt for a new loop
    loop = asyncio.get_running_loop()
    if _writers is None or _writers.loop is not loop:
        settings = context.settings
        _writers = _Writers(
            loop=loop,
            slots=asyncio.Semaphore(settings.gateway_record_concurrency),
            backlog=settings.gateway_record_backlog,
            backlog_bytes=settings.gateway_record_backlog_mb * 1024 * 1024,
        )
    return _writers


def _settle_lost(writers: _Writers, context: GatewayContext, span: dict[str, Any]) -> None:
    """Release the budget reservations of a span that will never be written, in the background
    (one short transaction; `drain` waits for it like a write). Not for an overflow: the backlog
    is full because the database is slow, so that settle waits for the reaper instead."""
    task = writers.loop.create_task(settle_unstored(context.sessions, context.project_id, span))
    writers.pending.add(task)
    task.add_done_callback(writers.pending.discard)


def _overflow(writers: _Writers, project_id: uuid.UUID) -> None:
    lose(project_id, "overflow")
    writers.dropped += 1
    now = time.monotonic()
    if writers.logged_at is None or now - writers.logged_at >= OVERFLOW_LOG_INTERVAL_S:
        logger.warning("gateway.record_overflow", dropped=writers.dropped, backlog=writers.backlog)
        writers.logged_at, writers.dropped = now, 0


__all__ = [
    "RECORD_RETRY_DELAYS_S",
    "DirectSpans",
    "credentials_used",
    "drain",
    "record",
    "record_soon",
    "spans_lost",
]

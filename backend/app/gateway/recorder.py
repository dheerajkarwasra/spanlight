"""Writing a gateway call's span, and what the call used, after the client has its answer.

`record` runs in a session of its own, so it never shares a transaction with the request (or
holds a lock the request needs); `app.gateway.span_write` says what one write stores.

A write that fails for a reason that may pass (a lost connection, a timeout, a deadlock: see
`app.core.db_errors.is_transient`) is retried after each of `RECORD_RETRY_DELAYS_S` (waiting
through the context's `sleep`); each try is one transaction. A rollback before commit leaves
nothing behind; an unacknowledged commit remains uncertain. A retry checks for a stored span
(a try whose commit went
through but was not acknowledged), so the key's minute counts the call once. Nothing is raised:
the client was already answered. A span still not stored after the last retry, or whose write
failed for a reason a retry would meet again (a deleted project, a value or constraint the
database refuses, a bug), is logged by the error's type and SQLSTATE, never its message, and
counted as `reason="error"` only when no commit remains unresolved. Uncertain writes emit
accounting-incomplete diagnostics without inventing a definite loss. A span ingestion refuses
is not retried (it would be refused
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
`GATEWAY_RECORD_BACKLOG_MB` (counted as their stored JSON bytes; a span still waiting for a
worker thread to prepare a large body counts the body's size), so the backlog's memory is
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
import time
import uuid
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import structlog

from app.budgets.reserve import settle_unstored
from app.core.thread_ownership import owned_thread
from app.gateway.cleanup_join import retained
from app.gateway.context import LARGE_BODY_BYTES, GatewayContext, GatewayResult
from app.gateway.record_tasks import RecorderShutdown, _Writers, shutdown_writers
from app.gateway.record_write import _record
from app.gateway.span_losses import lose, spans_lost
from app.gateway.span_write import write_span
from app.gateway.write_evidence import WriteEvidence
from app.ingest.normalize import prepare_span_payloads

logger = structlog.get_logger(__name__)

OVERFLOW_LOG_INTERVAL_S = 60.0
# The waits before each retry of a failed span write: four tries over about 2.6 s. A constant
# beside the `GATEWAY_RECORD_*` settings (`app.config`), which bound how many writes run and wait.
RECORD_RETRY_DELAYS_S = (0.1, 0.5, 2.0)
# Large payloads are prepared on a thread of their own, not the event loop's default executor
# (which also resolves hostnames for upstream calls). One thread: the work is Python, so the GIL
# lets no second one go faster, and a queue of preparations only delays spans, not answers.
_payload_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gateway-payloads")


_writers: _Writers | None = None


async def record(
    context: GatewayContext, span: dict[str, Any], credential_ids: Sequence[uuid.UUID] = ()
) -> bool:
    """Write the span and the key's usage, retrying a failed write. True when the span was stored.

    Never raises (see the module docstring): a lost or refused span is logged, counted and
    answered with False.
    """
    evidence = WriteEvidence()
    try:
        return await _record(
            context,
            span,
            credential_ids,
            slots=None,
            prepared_truncated=None,
            evidence=evidence,
            write=write_span,
            retry_delays=RECORD_RETRY_DELAYS_S,
        )
    except asyncio.CancelledError:
        _account_abandoned(context, evidence)
        raise
    finally:
        if evidence.reported:
            _writers_for(context).accounting_complete = False


def record_soon(
    context: GatewayContext,
    span: dict[str, Any],
    credential_ids: Sequence[uuid.UUID] = (),
    *,
    low_priority: bool = False,
    body_bytes: int = 0,
) -> None:
    """Run `record` in the background, or drop the span when the backlog is full (half full for
    a `low_priority` span). A context with a `span_sink` hands the span to it instead.

    A span whose request body (`body_bytes`) is over `LARGE_BODY_BYTES` has its payloads
    prepared in a worker thread: redacting and cutting a 10 MB body takes about half a second
    of Python, which on the event loop would stall every call on the process. Until then it
    counts `body_bytes` against the backlog's bytes.
    """
    if context.span_sink is not None:
        context.span_sink.add(span, credential_ids)
        return
    writers = _writers_for(context)
    limit = max(1, writers.backlog // 2) if low_priority else writers.backlog
    if len(writers.pending) >= limit:
        _overflow(writers, context.project_id)
        return
    in_thread = body_bytes > LARGE_BODY_BYTES
    truncated, size = False, body_bytes
    if not in_thread:
        # Held until written, so held as stored: redacted and at most 1 MiB per payload.
        try:
            span, truncated, size = _prepare(context, span)
        except Exception as failure:  # noqa: BLE001 - counted and logged, the client was answered
            _prepare_failed(writers, context, span, failure)
            return
    byte_limit = writers.backlog_bytes // 2 if low_priority else writers.backlog_bytes
    # The first waiting span is always taken, so one span larger than the budget is not lost.
    if writers.pending and writers.held_bytes + size > byte_limit:
        _overflow(writers, context.project_id)
        return
    evidence = WriteEvidence()

    async def prepare_then_write() -> None:
        nonlocal span, truncated, size
        if in_thread:
            raw = span
            try:
                work = writers.loop.run_in_executor(_payload_executor, _prepare, context, raw)
                span, truncated, prepared = await owned_thread(work)
            except Exception as failure:  # noqa: BLE001 - as on the event loop above
                _prepare_failed(writers, context, raw, failure)
                return
            writers.held_bytes += prepared - size
            size = prepared
        await _write(writers, context, span, credential_ids, truncated, evidence)

    task = writers.loop.create_task(prepare_then_write())
    writers.pending.add(task)
    writers.held_bytes += size

    def _release(done: "asyncio.Task[None]") -> None:
        if evidence.reported:
            writers.accounting_complete = False
        if done.cancelled():
            _account_abandoned(context, evidence)
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
    """Drain, then cancel and cooperatively join unfinished writers before returning.

    The timeout bounds ordinary work, not driver cancellation. Process owners needing a
    hard envelope use shutdown and must keep pools alive when its join is incomplete.
    """
    loop = asyncio.get_running_loop()
    await retained(shutdown(cancel_at=None if timeout_s is None else loop.time() + timeout_s))


def pending_tasks() -> tuple[asyncio.Task[None], ...]:
    """Remaining database users; retain pools until these tasks have joined."""
    writers = _writers
    if writers is None or writers.loop is not asyncio.get_running_loop():
        return ()
    return tuple(task for task in writers.pending if not task.done())


async def shutdown(*, cancel_at: float | None, deadline: float | None = None) -> RecorderShutdown:
    """Join recorder tasks after external producers stop; deadlines use loop.time()."""
    return await shutdown_writers(_writers, cancel_at=cancel_at, deadline=deadline)


async def _write(
    writers: _Writers,
    context: GatewayContext,
    span: dict[str, Any],
    credential_ids: Sequence[uuid.UUID],
    truncated: bool,
    evidence: WriteEvidence,
) -> None:
    await _record(
        context,
        span,
        credential_ids,
        slots=writers.slots,
        prepared_truncated=truncated,
        evidence=evidence,
        write=write_span,
        retry_delays=RECORD_RETRY_DELAYS_S,
    )


def _prepare(context: GatewayContext, span: dict[str, Any]) -> tuple[dict[str, Any], bool, int]:
    return prepare_span_payloads(
        span, capture_payloads=context.capture_payloads, redact_contacts=context.redact_contact_info
    )


def _prepare_failed(
    writers: _Writers, context: GatewayContext, span: dict[str, Any], failure: Exception
) -> None:
    """Counted like a write that never succeeds. Only the type is logged: the message may quote
    the payload."""
    lose(context.project_id, "error")
    logger.error(
        "gateway.record_failed",
        span_id=span.get("span_id"),
        stage="prepare",
        error_type=type(failure).__name__,
    )
    _settle_lost(writers, context, span)


def _account_abandoned(context: GatewayContext, evidence: WriteEvidence) -> None:
    if not evidence.abandoned(context.project_id):
        _writers_for(context).accounting_complete = False


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
    "RecorderShutdown",
    "credentials_used",
    "drain",
    "record",
    "record_soon",
    "spans_lost",
]

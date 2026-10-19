"""The `create_export` job: stream a project's matching traces, or its attribution table, to a
file in object storage (the attribution writer is `app.exports.attribution_job`).

The job reads under the project's row-level security, never with the bypass flag: the project id
in the payload is bound to every transaction it opens, so even a wrong filter cannot reach
another project's rows. Transaction-local settings end with each transaction, so every database
step opens its own short session and binds the project first. Reads also run under a statement
timeout, so one runaway query ends the export (`EXPORT_TIMEOUT`) instead of hanging it.

Memory stays flat. Traces are read in keyset batches, each trace is encoded to its own chunk and
handed to the store's streaming upload, and the upload sends 8 MiB parts as they fill. A JSONL
batch holds at most `MAX_SPANS_PER_BATCH` spans and `MAX_PAYLOAD_BYTES_PER_BATCH` of stored
payloads (summed over its traces), and a trace past either has its spans read in keyset chunks,
so nothing holds a whole export or a whole oversized trace.

The job runs in the worker's long-job slot, which leaves the lease to the task: a background beat
extends it every 20 s for as long as the work runs, so a single slow statement cannot let the job
be taken over, and a lease that is lost cancels the work.
"""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Export, ExportFormat, ExportKind, ExportStatus
from app.exports.attribution_job import attribution_batches
from app.exports.job_support import (
    STATEMENT_TIMEOUT_MS,
    ExportFailedError,
    Tally,
    TooLargeError,
    bound,
)
from app.exports.queries import count_traces_up_to, fetch_span_chunk, fetch_trace_batch
from app.exports.schemas import AttributionExportIn, ExportFilters, TraceExportRow
from app.exports.service import (
    CONTENT_TYPES,
    EXPORT_RETENTION,
    MAX_EXPORT_TRACES,
    storage_key,
)
from app.exports.writers import (
    JSONL_TAIL,
    csv_header,
    jsonl_head,
    jsonl_line,
    jsonl_span,
    trace_csv_line,
)
from app.jobs.context import TaskContext
from app.jobs.outcome import JobOutcome
from app.jobs.queue import LeaseLostError
from app.storage.object_store import ObjectStore, get_object_store

logger = structlog.get_logger(__name__)

# The most traces a batch reads. A JSONL batch is also cut by `MAX_SPANS_PER_BATCH`.
BATCH_SIZES: dict[ExportFormat, int] = {ExportFormat.CSV: 1000, ExportFormat.JSONL: 100}
# Spans carry prompts and completions (up to 1 MiB each side), so a batch is bounded by the
# payload bytes stored with them (`spans.payload_bytes`) as well as by spans, since attributes take
# up to 64 KB of each span whatever its payloads. An oversized trace is read in chunks of
# `SPAN_CHUNK_SIZE` spans: at most 64 MiB of payloads.
MAX_SPANS_PER_BATCH = 2000
MAX_PAYLOAD_BYTES_PER_BATCH = 64 * 1024 * 1024
SPAN_CHUNK_SIZE = 32
# A third of the 60 s lease: two missed beats still leave time before another worker may take over.
HEARTBEAT_INTERVAL_SECONDS = 20.0
_QUERY_CANCELED = "57014"

ERROR_NOT_CONFIGURED = "NOT_CONFIGURED"
ERROR_TIMEOUT = "EXPORT_TIMEOUT"
ERROR_FAILED = "EXPORT_FAILED"


@dataclass
class _Lease:
    """Whether the job's lease was lost, which the beat sets before it cancels the work."""

    lost: bool = False


@dataclass(frozen=True)
class _Request:
    """What the export row asked for: the trace filters, or the attribution request."""

    format: ExportFormat
    body: ExportFilters | AttributionExportIn


async def run_create_export(context: TaskContext, payload: dict[str, Any]) -> JobOutcome | None:
    export_id = uuid.UUID(payload["export_id"])
    project_id = uuid.UUID(payload["project_id"])
    factory = context.session_factory
    log = logger.bind(export_id=str(export_id), project_id=str(project_id))

    request = await _start(factory, project_id, export_id)
    if request is None:
        log.info("export_skipped", reason="not queued or running")
        return JobOutcome.OK

    store = get_object_store(context.settings)
    if store is None:
        await _fail(factory, project_id, export_id, ERROR_NOT_CONFIGURED)
        log.info("export_skipped", reason="object storage is not configured")
        return JobOutcome.SKIPPED_NOT_CONFIGURED

    lease = _Lease()
    try:
        await _with_lease(
            context,
            lease,
            _count_then_write(context, store, project_id, export_id, request),
        )
    except LeaseLostError:
        raise
    except ExportFailedError as failed:
        await _fail(factory, project_id, export_id, failed.code)
        log.info("export_failed", error_code=failed.code)
    except OperationalError as error:
        if getattr(error.orig, "sqlstate", None) != _QUERY_CANCELED:
            await _fail_if_last_attempt(context, project_id, export_id, lease)
            raise
        # A query that ran past the statement timeout will run past it again: no retry.
        await _fail(factory, project_id, export_id, ERROR_TIMEOUT)
        log.info("export_failed", error_code=ERROR_TIMEOUT)
    except BaseException:
        # A retry starts the file again; only the last attempt records the failure. This also
        # covers the worker's timeout, which arrives as a cancellation.
        await _fail_if_last_attempt(context, project_id, export_id, lease)
        raise
    return JobOutcome.OK


async def _fail_if_last_attempt(
    context: TaskContext, project_id: uuid.UUID, export_id: uuid.UUID, lease: _Lease
) -> None:
    if lease.lost or context.job.attempts < context.job.max_attempts:
        return
    with contextlib.suppress(Exception):
        await _fail(context.session_factory, project_id, export_id, ERROR_FAILED)


async def _with_lease(context: TaskContext, lease: _Lease, work: Coroutine[Any, Any, None]) -> None:
    """Run `work` while extending the job's lease; a lost lease cancels the work.

    The long-job slot leaves the lease to the task. The beat runs beside the work, not between
    its steps, so a statement that takes a minute cannot let the lease run out.
    """
    task = asyncio.create_task(work)
    beat = asyncio.create_task(_beat_until_lost(context, lease))
    try:
        await asyncio.wait({task, beat}, return_when=asyncio.FIRST_COMPLETED)
        if not task.done():
            # Only the beat can finish first, and only by raising LeaseLostError.
            await beat
        return task.result()
    finally:
        for running in (task, beat):
            running.cancel()
        for running in (task, beat):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await running


async def _beat_until_lost(context: TaskContext, lease: _Lease) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        try:
            await context.heartbeat()
        except LeaseLostError:
            lease.lost = True
            raise
        except Exception:
            # A database blip must not kill a healthy export; the next beat tries again.
            logger.warning("export_heartbeat_failed", exc_info=True)


async def _count_then_write(
    context: TaskContext,
    store: ObjectStore,
    project_id: uuid.UUID,
    export_id: uuid.UUID,
    request: _Request,
) -> None:
    factory = context.session_factory
    tally = Tally()
    if isinstance(request.body, AttributionExportIn):
        # The attribution table has no cheap count: the writer stops at the row cap instead.
        body = attribution_batches(factory, project_id, request.body, tally)
    else:
        async with bound(factory, project_id, statement_timeout_ms=STATEMENT_TIMEOUT_MS) as db:
            count = await count_traces_up_to(db, project_id, request.body, MAX_EXPORT_TRACES)
        if count > MAX_EXPORT_TRACES:
            raise TooLargeError
        body = _encoded_batches(factory, project_id, request.body, request.format, tally)

    # Every attempt writes its own key (see `storage_key`), so this attempt may always delete what
    # it wrote without asking whether another attempt owns the file.
    key = storage_key(project_id, export_id, request.format, context.job.fence)
    try:
        await store.put(key, body, CONTENT_TYPES[request.format])
        if not await _finish_uninterrupted(factory, project_id, export_id, key, tally):
            # The row is gone (its project was deleted) or was no longer running (failed by the
            # reaper, or finished by another attempt): nothing points at this object.
            await _discard(store, key)
            return
    except BaseException:
        # Whatever went wrong, including a lost lease, this attempt's object must not stay behind
        # unless the row already points at it. If a delete fails, the object is found later by its
        # prefix: the reaper and the expiry job delete everything under the export's prefix, and
        # so does the project purge.
        if not tally.recorded and await _is_unreferenced(factory, project_id, export_id, key):
            await _discard(store, key)
        raise
    logger.info(
        "export_done",
        export_id=str(export_id),
        project_id=str(project_id),
        rows=tally.rows,
        size_bytes=tally.size_bytes,
    )


async def _encoded_batches(
    factory: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
    filters: ExportFilters,
    export_format: ExportFormat,
    tally: Tally,
) -> AsyncIterator[bytes]:
    """The file's bytes, one chunk per trace, tallying rows and size as it goes."""
    csv = export_format is ExportFormat.CSV
    size = BATCH_SIZES[export_format]
    if csv:
        header = csv_header()
        tally.size_bytes += len(header)
        yield header
    after: tuple[datetime, str] | None = None
    while True:
        async with bound(factory, project_id, statement_timeout_ms=STATEMENT_TIMEOUT_MS) as db:
            rows = await fetch_trace_batch(
                db,
                project_id,
                filters,
                after=after,
                size=size,
                max_spans=None if csv else MAX_SPANS_PER_BATCH,
                max_payload_bytes=MAX_PAYLOAD_BYTES_PER_BATCH,
            )
        if not rows:
            return
        for row in rows:
            tally.rows += 1
            if tally.rows > MAX_EXPORT_TRACES:
                raise TooLargeError
            if csv:
                chunk = trace_csv_line(row)
                tally.size_bytes += len(chunk)
                yield chunk
            elif row.oversized:
                async for chunk in _oversized_trace_lines(factory, project_id, row):
                    tally.size_bytes += len(chunk)
                    yield chunk
            else:
                chunk = jsonl_line(row)
                tally.size_bytes += len(chunk)
                yield chunk
        # A JSONL batch can be cut short by the span budget, so only an empty read ends it.
        if csv and len(rows) < size:
            return
        after = (rows[-1].started_at, rows[-1].trace_id)


async def _oversized_trace_lines(
    factory: async_sessionmaker[AsyncSession], project_id: uuid.UUID, row: TraceExportRow
) -> AsyncIterator[bytes]:
    """One trace line whose spans are read in keyset chunks instead of all at once."""
    yield jsonl_head(row)
    first = True
    after: tuple[datetime, str] | None = None
    while True:
        async with bound(factory, project_id, statement_timeout_ms=STATEMENT_TIMEOUT_MS) as db:
            spans = await fetch_span_chunk(
                db, project_id, row.trace_id, after=after, size=SPAN_CHUNK_SIZE
            )
        for span in spans:
            yield jsonl_span(span, first=first)
            first = False
        if len(spans) < SPAN_CHUNK_SIZE:
            break
        after = (spans[-1].started_at, spans[-1].span_id)
    yield JSONL_TAIL


async def _start(
    factory: async_sessionmaker[AsyncSession], project_id: uuid.UUID, export_id: uuid.UUID
) -> _Request | None:
    """Mark a queued (or interrupted, running) export as running; None if there is nothing to do.

    None covers an export that is gone (its project was deleted) and one that already finished,
    which a redelivered job must not redo.
    """
    async with bound(factory, project_id) as db:
        export = await db.get(Export, export_id, with_for_update=True)
        if export is None or export.project_id != project_id:
            return None
        if export.status not in (ExportStatus.QUEUED, ExportStatus.RUNNING):
            return None
        export.status = ExportStatus.RUNNING
        body: ExportFilters | AttributionExportIn
        if export.kind is ExportKind.ATTRIBUTION:
            stored = {"kind": export.kind, "format": export.format, **export.filters}
            body = AttributionExportIn.model_validate(stored)
        else:
            body = ExportFilters.model_validate(export.filters)
        request = _Request(export.format, body)
        await db.commit()
    return request


async def _finish(
    factory: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
    export_id: uuid.UUID,
    key: str,
    tally: Tally,
) -> bool:
    """Record the finished export; False if its row is gone or no longer running (reaped).

    Sets `tally.recorded` right after the commit, before anything else can interrupt.
    """
    now = datetime.now(UTC)
    async with bound(factory, project_id) as db:
        result = await db.execute(
            update(Export)
            .where(
                Export.id == export_id,
                Export.project_id == project_id,
                # A worker that lost its lease must not flip a row the reaper already failed.
                Export.status == ExportStatus.RUNNING,
            )
            .values(
                status=ExportStatus.DONE,
                storage_key=key,
                size_bytes=tally.size_bytes,
                row_count=tally.rows,
                error_code=None,
                completed_at=now,
                expires_at=now + EXPORT_RETENTION,
            )
        )
        await db.commit()
        tally.recorded = bool(getattr(result, "rowcount", 0))
    return tally.recorded


async def _finish_uninterrupted(
    factory: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
    export_id: uuid.UUID,
    key: str,
    tally: Tally,
) -> bool:
    """`_finish`, but a cancellation cannot interrupt it half-way.

    A lost lease or a timeout cancels the work. If that landed between the UPDATE and its commit,
    the row could be left pointing at an object the attempt then deletes. The update runs as its
    own task: on cancellation this waits for it to end, then lets the cancellation continue.
    """
    task = asyncio.ensure_future(_finish(factory, project_id, export_id, key, tally))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.wait({task})
        raise


async def _is_unreferenced(
    factory: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
    export_id: uuid.UUID,
    key: str,
) -> bool:
    """Whether no export row points at `key`, so the attempt may delete the object.

    True when the row is gone or names another key. If the row cannot be read the answer is False:
    keeping a possibly live object is better than a finished export whose download is dead, and
    the expiry job or the project purge deletes a leaked one later.
    """
    try:
        async with bound(factory, project_id) as db:
            row = (
                await db.execute(
                    select(Export.id, Export.storage_key).where(
                        Export.id == export_id, Export.project_id == project_id
                    )
                )
            ).one_or_none()
    except Exception:
        logger.warning("export_reference_check_failed", exc_info=True)
        return False
    return row is None or row.storage_key != key


async def _fail(
    factory: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
    export_id: uuid.UUID,
    error_code: str,
) -> None:
    async with bound(factory, project_id) as db:
        await db.execute(
            update(Export)
            .where(
                Export.id == export_id,
                Export.project_id == project_id,
                Export.status == ExportStatus.RUNNING,
            )
            .values(
                status=ExportStatus.FAILED, error_code=error_code, completed_at=datetime.now(UTC)
            )
        )
        await db.commit()


async def _discard(store: ObjectStore, key: str) -> bool:
    """Delete a half-finished or orphaned object; whether it worked. Never raises.

    The original error matters more than a failed delete.
    """
    try:
        await store.delete(key)
    except Exception:
        logger.warning("export_discard_failed", key=key, exc_info=True)
        return False
    return True

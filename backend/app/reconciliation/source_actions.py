"""A billing source's manual refresh and CSV upload (spec §8.2, §8.4, §10.2). Callers commit.

A refresh is checked in a fixed order: the source exists, fetches from a provider, is enabled,
provider calls are on and the server can open its key (`check_refresh`). The router then takes a
token from the source's refresh bucket, and `start_refresh` opens the run and queues its fetch
in one transaction. The run is opened under the source's row lock (`runs.open_run`), so of two
refreshes at once one opens the run and the other gets `RunInProgress`; the job's dedupe key
names the run, so each run is queued once and the job carries on the run the refresh opened.

An upload is parsed in full before anything is written, and any error refuses the whole file
(`CsvRejected`). One transaction then opens the run, upserts the lines and closes the run `ok`.
The Spanlight side of the mapped projects is then recomputed over the file's days, so the report
compares the file with fresh figures: inline after the commit (`refresh_upload_rollups`) when
the days span at most `INLINE_ROLLUP_DAYS`, else by `rollup_cost_daily_backfill` jobs queued in
the upload's transaction, one per project and chunk of at most 31 days, so a file with a line in
January and one in October never scans nine months of spans inside the request.

Every write takes the organization's write lock first, then the source row: the lock order of
`service`, so a concurrent organization deletion waits instead of deadlocking. The file's bytes
live only in the caller's memory: they are not stored, logged or audited; the audit event
records counts.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

import structlog
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.db.models import AuditAction
from app.db.models.billing import BillingFetchRun, BillingSource, FetchTrigger
from app.db.rls import bind_project
from app.jobs.queue import enqueue
from app.reconciliation import queries
from app.reconciliation.backfill import (
    BACKFILL_ATTEMPTS,
    BACKFILL_JOB,
    MAX_BACKFILL_DAYS,
    backfill_payload,
    day_chunks,
)
from app.reconciliation.csv_import import CsvErrors, ParsedCsv, parse_cost_csv
from app.reconciliation.errors import (
    BillingSourceNotFound,
    CsvRejected,
    NotApiKind,
    NotCsvKind,
    ProviderCallsOff,
    SourceDisabled,
)
from app.reconciliation.fetch_policy import MAX_ATTEMPTS, fetch_window
from app.reconciliation.rollups import refresh_cost_rollups
from app.reconciliation.runs import (
    FETCH_SOURCE_JOB,
    SourceGone,
    child_payload,
    finish_run_ok,
    open_run,
    write_lines,
)
from app.reconciliation.service import audit_source
from app.reconciliation.source_keys import require_crypto
from app.reconciliation.source_rules import is_api_kind
from app.services.deletion import lock_org_for_write

logger = structlog.get_logger(__name__)

# The longest span of days an upload recomputes inside its request; a longer one is queued.
INLINE_ROLLUP_DAYS = MAX_BACKFILL_DAYS


@dataclass(frozen=True)
class Uploaded:
    """A stored upload: its run, the lines it wrote, the days they cover (ascending), the
    projects the source is mapped to, and whether their rollups are left to the caller
    (`refresh_upload_rollups`) or were queued as backfills."""

    run_id: uuid.UUID
    lines_upserted: int
    days: list[date]
    project_ids: list[uuid.UUID]
    rollups_inline: bool


async def check_refresh(
    db: AsyncSession, org_id: uuid.UUID, source_id: uuid.UUID, *, settings: Settings
) -> BillingSource:
    """The source, if a refresh of it may start. Raises, in this order, `BillingSourceNotFound`,
    `NotApiKind`, `SourceDisabled`, `ProviderCallsOff` and `CryptoNotConfigured`."""
    source = await queries.source_for_org(db, org_id, source_id)
    if source is None:
        raise BillingSourceNotFound
    if not is_api_kind(source.kind):
        raise NotApiKind
    if not source.enabled:
        raise SourceDisabled
    if not settings.billing_sources_enabled:
        raise ProviderCallsOff
    require_crypto(settings)
    return source


async def start_refresh(
    db: AsyncSession,
    source: BillingSource,
    actor_id: uuid.UUID,
    *,
    now: datetime,
    ip: str | None = None,
) -> BillingFetchRun:
    """Open a `manual` run of the source and queue the fetch that carries it on.

    The window is the scheduled fetch's: the last week, or a month for a source never fetched
    successfully. Raises `BillingSourceNotFound`, `SourceDisabled` (turned off since
    `check_refresh`) and `RunInProgress`.
    """
    locked = await _lock_source(db, source)
    if not locked.enabled:
        raise SourceDisabled
    day_from, day_to = fetch_window(now, first_fetch=locked.last_success_at is None)
    run = await _open(db, locked, "manual", day_from, day_to, actor_id)
    await enqueue(
        db,
        FETCH_SOURCE_JOB,
        child_payload(
            locked.id, day_from, day_to, trigger="manual", requested_by=actor_id, run_id=run.id
        ),
        dedupe_key=f"{FETCH_SOURCE_JOB}:{locked.id}:manual:{run.id}",
        max_attempts=MAX_ATTEMPTS,
    )
    await audit_source(
        db,
        locked,
        AuditAction.BILLING_SOURCE_REFRESH,
        actor_id,
        ip,
        extra={"run_id": str(run.id)},
    )
    return run


async def csv_source(db: AsyncSession, org_id: uuid.UUID, source_id: uuid.UUID) -> BillingSource:
    """The source, if it takes uploads. Raises `BillingSourceNotFound` and `NotCsvKind`."""
    source = await queries.source_for_org(db, org_id, source_id)
    if source is None:
        raise BillingSourceNotFound
    if is_api_kind(source.kind):
        raise NotCsvKind
    return source


async def upload_csv(
    db: AsyncSession,
    source: BillingSource,
    data: bytes,
    actor_id: uuid.UUID,
    *,
    now: datetime,
    ip: str | None = None,
) -> Uploaded:
    """Parse the file and store its lines as one `csv_upload` run of the source.

    A line already stored for the same day and key takes the file's figures; every day must lie
    in the 400 days up to `now`'s UTC date. Raises `CsvRejected` (nothing written),
    `BillingSourceNotFound` and `RunInProgress`.
    """
    parsed = await _parse(db, source, data, now.astimezone(UTC).date())
    locked = await _lock_source(db, source)
    run = await _open(db, locked, "csv_upload", parsed.days[0], parsed.days[-1], actor_id)
    written = await write_lines(db, run, parsed.lines)
    try:
        # No provider page was read: the file is the whole of the run.
        await finish_run_ok(db, run, lines_upserted=written, pages=0)
    except SourceGone:
        raise BillingSourceNotFound from None
    await audit_source(
        db,
        locked,
        AuditAction.BILLING_SOURCE_UPLOAD,
        actor_id,
        ip,
        extra={"run_id": str(run.id), "lines": written, "days": len(parsed.days)},
    )
    project_ids = await queries.projects_of_source(db, locked.org_id, locked.id)
    inline = (parsed.days[-1] - parsed.days[0]).days < INLINE_ROLLUP_DAYS
    if not inline:
        await _queue_backfills(db, run.id, project_ids, parsed.days)
    return Uploaded(
        run_id=run.id,
        lines_upserted=written,
        days=parsed.days,
        project_ids=project_ids,
        rollups_inline=inline,
    )


async def refresh_upload_rollups(db: AsyncSession, uploaded: Uploaded) -> None:
    """Recompute the cost rollups of the source's projects over the upload's days, each bound
    to its project and committed on its own. Call it after the upload committed.

    A project whose recompute fails is logged and skipped: the upload is stored already, the
    rollup passes and the report's lazy build recompute the days again, and an upload that
    answered an error would be sent again for nothing. That includes a day at the end of the
    calendar, whose next midnight overflows (`OverflowError`); the parser refuses such days, so
    this is defence in depth. Does nothing for an upload whose rollups were queued.
    """
    if not uploaded.rollups_inline:
        return
    for project_id in uploaded.project_ids:
        try:
            await bind_project(db, project_id)
            await refresh_cost_rollups(db, project_id, uploaded.days)
            await db.commit()
        except (SQLAlchemyError, OverflowError) as exc:
            await db.rollback()
            logger.warning(
                "billing_upload_rollups_failed",
                project_id=str(project_id),
                run_id=str(uploaded.run_id),
                error_type=type(exc).__name__,
            )


async def _queue_backfills(
    db: AsyncSession, run_id: uuid.UUID, project_ids: Sequence[uuid.UUID], days: Sequence[date]
) -> None:
    """One backfill per project and chunk of the days, deduplicated per run and chunk."""
    for project_id in project_ids:
        for day_from, day_to in day_chunks(days):
            await enqueue(
                db,
                BACKFILL_JOB,
                backfill_payload(project_id, day_from, day_to),
                dedupe_key=f"{BACKFILL_JOB}:{project_id}:{run_id}:{day_from.isoformat()}",
                max_attempts=BACKFILL_ATTEMPTS,
            )


async def _parse(db: AsyncSession, source: BillingSource, data: bytes, today: date) -> ParsedCsv:
    patterns = await queries.mapping_patterns(db, source.org_id)
    # Parsing 5 MB is CPU work that would otherwise stall the event loop for every tenant.
    parsed = await run_in_threadpool(
        parse_cost_csv, data, provider=source.provider, patterns=patterns, today=today
    )
    if isinstance(parsed, CsvErrors):
        raise CsvRejected(parsed.errors)
    return parsed


async def _lock_source(db: AsyncSession, source: BillingSource) -> BillingSource:
    """The source as it is under the organization's write lock and its own row lock."""
    if not await lock_org_for_write(db, source.org_id):
        raise BillingSourceNotFound
    locked = await queries.source_for_org(db, source.org_id, source.id, lock=True)
    if locked is None:
        raise BillingSourceNotFound
    return locked


async def _open(
    db: AsyncSession,
    source: BillingSource,
    trigger: FetchTrigger,
    day_from: date,
    day_to: date,
    actor_id: uuid.UUID,
) -> BillingFetchRun:
    try:
        return await open_run(
            db, source, trigger=trigger, day_from=day_from, day_to=day_to, requested_by=actor_id
        )
    except SourceGone:
        raise BillingSourceNotFound from None

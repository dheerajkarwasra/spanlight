"""The I/O shell around the pure feed adapters: fetch, parse, upsert, record.

`poll_one` polls one adapter's feed and always records the outcome on the feed's source row:

- 200 → parse → upsert every incident and record `fetched`, in one transaction.
- 304 (the conditional GET matched the stored validators) → `not_modified`; incidents untouched.
- Any failure → its outcome (`http_error`, `parse_error`, `too_large`, `timeout`, `blocked`),
  the failure count incremented, incidents untouched. An adapter with a fallback feed (OpenAI's
  RSS) tries it once first, unless the host was blocked; a parsed fallback counts as `fetched`
  and clears the stored validators, so the next primary request is unconditional.
- A database error while storing → rolled back, then `parse_error` with the error's class name
  recorded in a fresh transaction: rows the database refuses are a feed the poller cannot store.

Only the adapter's constant URLs are fetched, never the `url` column of the source row. Nothing
here logs: the job writes one line per adapter from the returned `PollResult`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProviderStatusSource
from app.provider_status.adapters import FeedAdapter, FeedParseError, ParsedIncident, ParseReport
from app.provider_status.adapters.types import ParseResult
from app.provider_status.fetch import MAX_FEED_BYTES, Failed, Fetched, fetch_feed
from app.provider_status.outcome import PollOutcome
from app.provider_status.service import record_poll, upsert_incidents

__all__ = ["MAX_FEED_BYTES", "PollOutcome", "PollResult", "poll_one"]


@dataclass(frozen=True, slots=True)
class PollResult:
    """How one poll went. `attempts` holds the outcome of every request made, in order (two when
    the fallback feed was tried); `outcome` is the one recorded on the source row."""

    outcome: PollOutcome
    attempts: tuple[PollOutcome, ...]
    source: str | None = None
    error_type: str | None = None
    status_code: int | None = None
    report: ParseReport | None = None
    upserted: int = 0

    @property
    def fell_back_from(self) -> PollOutcome | None:
        """The primary feed's outcome when the fallback feed was tried, else None."""
        return self.attempts[0] if len(self.attempts) > 1 else None


@dataclass(frozen=True, slots=True)
class _Parsed:
    incidents: list[ParsedIncident]
    report: ParseReport
    response: Fetched


@dataclass(frozen=True, slots=True)
class _Validators:
    etag: str | None
    last_modified: str | None


async def poll_one(
    client: httpx.AsyncClient, db: AsyncSession, adapter: FeedAdapter, *, now: datetime
) -> PollResult:
    """Poll `adapter`'s feed once and record the outcome. Commits; raises only when recording
    the outcome itself fails (the database is unreachable)."""
    provider = adapter.provider
    stored = await _stored_validators(db, provider)
    primary = await _fetch_and_parse(
        client, adapter.url, adapter.parse_with_report, validators=stored
    )
    if isinstance(primary, _Parsed):
        validators = _Validators(primary.response.etag, primary.response.last_modified)
        return await _store(db, adapter, primary, adapter.source, validators, (), now=now)
    if isinstance(primary, Fetched):
        outcome = PollOutcome.NOT_MODIFIED
        return await _record(db, provider, (outcome,), now=now, status=primary.status_code)
    if adapter.fallback_url is None or primary.outcome is PollOutcome.BLOCKED:
        return await _record_failure(db, provider, primary, (primary.outcome,), now=now)
    fallback = await _fetch_and_parse(
        client, adapter.fallback_url, adapter.parse_fallback_with_report, validators=None
    )
    if isinstance(fallback, _Parsed):
        # The fallback wrote rows the primary feed has not confirmed: clear the validators, so
        # the next primary request is unconditional and a 304 cannot keep the fallback's values.
        source = adapter.fallback_source or adapter.source
        cleared = _Validators(None, None)
        return await _store(db, adapter, fallback, source, cleared, (primary.outcome,), now=now)
    attempts = (primary.outcome, _outcome_of(fallback))
    return await _record_failure(db, provider, primary, attempts, now=now)


async def _stored_validators(db: AsyncSession, provider: str) -> _Validators:
    """The validators of the last successful fetch. Ends the read transaction, so none is held
    open while the feed is fetched."""
    row = (
        await db.execute(
            select(ProviderStatusSource.etag, ProviderStatusSource.last_modified).where(
                ProviderStatusSource.provider == provider
            )
        )
    ).one_or_none()
    await db.commit()
    return _Validators(None, None) if row is None else _Validators(row.etag, row.last_modified)


async def _fetch_and_parse(
    client: httpx.AsyncClient,
    url: str,
    parse: Callable[[bytes], ParseResult],
    *,
    validators: _Validators | None,
) -> _Parsed | Fetched | Failed:
    """A parsed 200, a bodiless 304 (`Fetched`), or the failure."""
    response = await fetch_feed(
        client,
        url,
        etag=validators.etag if validators else None,
        last_modified=validators.last_modified if validators else None,
    )
    if isinstance(response, Failed) or response.body is None:
        return response
    try:
        incidents, report = parse(response.body)
    except FeedParseError as error:
        return Failed(PollOutcome.PARSE_ERROR, type(error).__name__, response.status_code)
    return _Parsed(incidents, report, response)


async def _store(
    db: AsyncSession,
    adapter: FeedAdapter,
    parsed: _Parsed,
    source: str,
    validators: _Validators,
    earlier: tuple[PollOutcome, ...],
    *,
    now: datetime,
) -> PollResult:
    """Store a parsed feed; `earlier` holds the outcome of a primary request that failed before
    this fallback. A database error is rolled back and recorded as `parse_error`."""
    status = parsed.response.status_code
    try:
        upserted = await _write_fetched(
            db, adapter.provider, parsed, source, validators, now=now, from_fallback=bool(earlier)
        )
    except SQLAlchemyError as error:
        await db.rollback()
        failure = Failed(PollOutcome.PARSE_ERROR, type(error).__name__, status)
        attempts = (*earlier, PollOutcome.PARSE_ERROR)
        return await _record_failure(db, adapter.provider, failure, attempts, now=now)
    attempts = (*earlier, PollOutcome.FETCHED)
    return PollResult(PollOutcome.FETCHED, attempts, source, None, status, parsed.report, upserted)


async def _write_fetched(
    db: AsyncSession,
    provider: str,
    parsed: _Parsed,
    source: str,
    validators: _Validators,
    *,
    now: datetime,
    from_fallback: bool,
) -> int:
    """Upsert the incidents and record `fetched`, in one transaction. Returns the row count."""
    upserted = await upsert_incidents(
        db, provider, parsed.incidents, source=source, now=now, from_fallback=from_fallback
    )
    await record_poll(
        db,
        provider,
        PollOutcome.FETCHED,
        etag=validators.etag,
        last_modified=validators.last_modified,
        error_type=None,
        now=now,
    )
    await db.commit()
    return upserted


async def _record_failure(
    db: AsyncSession,
    provider: str,
    failure: Failed,
    attempts: tuple[PollOutcome, ...],
    *,
    now: datetime,
) -> PollResult:
    return await _record(
        db, provider, attempts, now=now, status=failure.status_code, failure=failure
    )


async def _record(
    db: AsyncSession,
    provider: str,
    attempts: tuple[PollOutcome, ...],
    *,
    now: datetime,
    status: int | None,
    failure: Failed | None = None,
) -> PollResult:
    """Record a poll that stored nothing: a 304 (`failure` None) or a failure."""
    outcome = failure.outcome if failure else PollOutcome.NOT_MODIFIED
    error_type = failure.error_type if failure else None
    await record_poll(
        db, provider, outcome, etag=None, last_modified=None, error_type=error_type, now=now
    )
    await db.commit()
    return PollResult(outcome, attempts, error_type=error_type, status_code=status)


def _outcome_of(result: Fetched | Failed) -> PollOutcome:
    # A fallback answered 304 without being asked conditionally: no body, so nothing to store.
    return result.outcome if isinstance(result, Failed) else PollOutcome.NOT_MODIFIED

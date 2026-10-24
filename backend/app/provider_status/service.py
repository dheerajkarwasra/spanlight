"""The provider status domain's service: the poller's writes and the reads other domains use.

The writes (the incident upsert and the source row's poll state) never commit; the poller runs
both in one transaction per feed. The reads are what the API and the detectors call: they never
import `queries` directly, so the domain's tables have one way in. Both tables
are global and carry no row-level security, so nothing here binds or bypasses it.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.db.models import ProviderIncident, ProviderStatusSource
from app.provider_status import queries
from app.provider_status.adapters import ParsedIncident
from app.provider_status.outcome import PollOutcome
from app.provider_status.types import DETECTOR_GRACE, ProviderIncidentRow, TimeRange

# What a feed may change on an incident it already sent: the row follows the feed, including a
# `resolved_at` that a reopened incident clears. `first_seen_at` and `id` never change.
_FOLLOWS_THE_FEED = (
    "source",
    "title",
    "impact",
    "status",
    "api_affected",
    "components",
    "started_at",
    "resolved_at",
    "url",
    "latest_update",
    "feed_updated_at",
    "fetched_at",
)
# A fallback feed (OpenAI's RSS) has no impact and no start time (its items are the newest
# update, dated by that update): it updates a row the primary feed wrote without overwriting
# the primary's source, impact or `started_at`; the next primary poll writes them again.
_KEPT_FROM_THE_PRIMARY = frozenset({"source", "impact", "started_at"})
# A fallback item whose status line was not recognised (`unknown`) says nothing about whether
# the incident is over: the row keeps the status and end the primary feed wrote.
_KEPT_UNLESS_THE_FALLBACK_KNOWS = ("status", "resolved_at")

_SUCCESS = frozenset({PollOutcome.FETCHED, PollOutcome.NOT_MODIFIED})


async def upsert_incidents(
    db: AsyncSession,
    provider: str,
    incidents: Sequence[ParsedIncident],
    *,
    source: str,
    now: datetime,
    from_fallback: bool = False,
) -> int:
    """Insert or update every incident on `(provider, external_id)`; returns how many rows.

    An `external_id` the feed repeats keeps its later entry: one multi-row
    `ON CONFLICT DO UPDATE` may not touch a row twice. Rows go in `external_id` order, so two
    concurrent polls lock them in the same order.
    """
    rows = _rows(provider, incidents, source=source, now=now)
    if not rows:
        return 0
    statement = insert(ProviderIncident).values(rows)
    excluded = statement.excluded
    set_: dict[str, Any] = {
        column: excluded[column]
        for column in _FOLLOWS_THE_FEED
        if not (from_fallback and column in _KEPT_FROM_THE_PRIMARY)
    }
    if from_fallback:
        for column in _KEPT_UNLESS_THE_FALLBACK_KNOWS:
            stored = getattr(ProviderIncident, column)
            set_[column] = case((excluded.status == "unknown", stored), else_=excluded[column])
    statement = statement.on_conflict_do_update(
        constraint="provider_incidents_provider_external_id_key", set_=set_
    )
    await db.execute(statement)
    return len(rows)


def _rows(
    provider: str, incidents: Sequence[ParsedIncident], *, source: str, now: datetime
) -> list[dict[str, Any]]:
    latest = {incident.external_id: incident for incident in incidents}
    return [
        {
            "id": new_id(),
            "provider": provider,
            "source": source,
            "external_id": incident.external_id,
            "title": incident.title,
            "impact": incident.impact,
            "status": incident.status,
            "api_affected": incident.api_affected,
            "components": list(incident.components),
            # A start after the feed's own end (an estimated start, or a feed contradicting
            # itself) is clamped to the end: the end is what the feed states, and dropping the
            # row would lose a real incident.
            "started_at": incident.started_at
            if incident.resolved_at is None
            else min(incident.started_at, incident.resolved_at),
            "resolved_at": incident.resolved_at,
            "url": incident.url,
            "latest_update": incident.latest_update,
            "feed_updated_at": incident.feed_updated_at,
            "fetched_at": now,
        }
        for _, incident in sorted(latest.items())
    ]


async def record_poll(
    db: AsyncSession,
    provider: str,
    outcome: PollOutcome,
    *,
    etag: str | None,
    last_modified: str | None,
    error_type: str | None,
    now: datetime,
) -> None:
    """Write how the poll of `provider`'s feed went.

    `fetched` stores the response's validators (`etag`, `last_modified`); every other outcome
    keeps the stored ones. A success (`fetched`, or `not_modified`: the feed answered that
    nothing changed) sets `last_success_at` and clears the failure count; a failure increments
    it and stores `error_type`, an exception class name.
    """
    values: dict[str, Any] = {"last_polled_at": now, "last_outcome": outcome.value}
    if outcome in _SUCCESS:
        values |= {"last_success_at": now, "consecutive_failures": 0, "last_error_type": None}
    else:
        values |= {
            "consecutive_failures": ProviderStatusSource.consecutive_failures + 1,
            "last_error_type": error_type,
        }
    if outcome is PollOutcome.FETCHED:
        values |= {"etag": etag, "last_modified": last_modified}
    await db.execute(
        update(ProviderStatusSource)
        .where(ProviderStatusSource.provider == provider)
        .values(**values)
    )


async def list_incidents(
    db: AsyncSession,
    *,
    window: TimeRange,
    provider: str | None,
    api_only: bool,
    limit: int,
    after: tuple[datetime, uuid.UUID] | None,
) -> list[ProviderIncident]:
    """Incidents overlapping `window`, newest first by `(started_at, id)`, after the cursor."""
    return await queries.list_incidents(
        db, window=window, provider=provider, api_only=api_only, limit=limit, after=after
    )


async def list_sources(db: AsyncSession) -> list[ProviderStatusSource]:
    """Every status feed with its poll state, by provider."""
    return await queries.list_sources(db)


async def overlapping_api_incidents(
    db: AsyncSession,
    *,
    provider: str,
    start: datetime,
    end: datetime,
    now: datetime,
    grace: timedelta = DETECTOR_GRACE,
) -> list[ProviderIncidentRow]:
    """`provider`'s API-affecting incidents overlapping `[start, end)` with `grace` added to
    each incident's end, newest first. An open incident counts as ending at `now`."""
    return await queries.overlapping_api_incidents(
        db, provider=provider, start=start, end=end, now=now, grace=grace
    )

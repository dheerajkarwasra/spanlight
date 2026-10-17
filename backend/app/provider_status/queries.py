"""Reads of the provider incidents and the status feeds' poll state.

Both tables are global (no tenant column, no row-level security): any session reads them, a
project-bound one included, and nothing here binds or bypasses anything. Other domains call
these through `app.provider_status.service`, never directly.

An incident overlaps a window `[start, end)` when it started before `end` and had not ended by
`start`: `started_at < end AND coalesce(resolved_at, now) > start`. The detectors
add a grace period to the end of the incident, for errors that drain after the feed resolves it.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ProviderIncident, ProviderStatusSource
from app.provider_status.types import DETECTOR_GRACE, ProviderIncidentRow, TimeRange

# More incidents than this overlapping one provider's 30-minute window means the feed is broken.
MAX_OVERLAPPING = 20


async def list_incidents(
    db: AsyncSession,
    *,
    window: TimeRange,
    provider: str | None,
    api_only: bool,
    limit: int,
    after: tuple[datetime, uuid.UUID] | None,
) -> list[ProviderIncident]:
    """Incidents overlapping `window`, newest first by `(started_at, id)`, after the cursor.

    An incident still open overlaps every window that starts before now (the database's).
    """
    query = select(ProviderIncident).where(
        ProviderIncident.started_at < window.end,
        func.coalesce(ProviderIncident.resolved_at, func.now()) > window.start,
    )
    if provider is not None:
        query = query.where(ProviderIncident.provider == provider)
    if api_only:
        query = query.where(ProviderIncident.api_affected.is_(True))
    if after is not None:
        query = query.where(
            tuple_(ProviderIncident.started_at, ProviderIncident.id) < tuple_(*after)
        )
    query = query.order_by(ProviderIncident.started_at.desc(), ProviderIncident.id.desc())
    return list((await db.scalars(query.limit(limit))).all())


async def list_sources(db: AsyncSession) -> list[ProviderStatusSource]:
    """Every status feed with its poll state, by provider."""
    query = select(ProviderStatusSource).order_by(ProviderStatusSource.provider)
    return list((await db.scalars(query)).all())


async def overlapping_api_incidents(
    db: AsyncSession,
    *,
    provider: str,
    start: datetime,
    end: datetime,
    now: datetime,
    grace: timedelta = DETECTOR_GRACE,
) -> list[ProviderIncidentRow]:
    """`provider`'s API-affecting incidents overlapping `[start, end)` once each is extended by
    `grace`, newest first. An open incident counts as ending at `now`."""
    query = (
        select(
            ProviderIncident.id,
            ProviderIncident.provider,
            ProviderIncident.title,
            ProviderIncident.url,
            ProviderIncident.impact,
            ProviderIncident.started_at,
            ProviderIncident.resolved_at,
        )
        .where(
            ProviderIncident.provider == provider,
            ProviderIncident.api_affected.is_(True),
            ProviderIncident.started_at < end,
            func.coalesce(ProviderIncident.resolved_at, now) > start - grace,
        )
        .order_by(ProviderIncident.started_at.desc(), ProviderIncident.id.desc())
        .limit(MAX_OVERLAPPING)
    )
    return [ProviderIncidentRow(*row) for row in (await db.execute(query)).all()]

"""The values the trace list's filter selectors offer: distinct, sorted, capped at 100.

Environments, releases, models, tags and score names are read over the last 30 days. Metadata
keys and trace names are read over 7 days: `jsonb_object_keys` over 30 days of traces is a scan
the other columns do not pay (spec §6.2). Every query names the project explicitly; row-level
security (the caller binds the session) filters it too.
"""

import uuid
from datetime import timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from app.api.deps import utcnow
from app.api.schemas import FiltersOut
from app.db.models import Score, Span, Trace

FILTER_LOOKBACK = timedelta(days=30)
METADATA_LOOKBACK = timedelta(days=7)
MAX_FILTER_VALUES = 100


async def _distinct_values(
    db: AsyncSession, column: InstrumentedAttribute[str | None], *conditions: ColumnElement[bool]
) -> list[str]:
    result = await db.scalars(
        select(column)
        .where(*conditions, column.is_not(None))
        .distinct()
        .order_by(column)
        .limit(MAX_FILTER_VALUES)
    )
    return [value for value in result.all() if value is not None]


async def _distinct_unnested(
    db: AsyncSession, unnested: ColumnElement[str], *conditions: ColumnElement[bool]
) -> list[str]:
    """Distinct values of a set-returning expression (`unnest`, `jsonb_object_keys`)."""
    inner = select(unnested.label("value")).where(*conditions).subquery()
    result = await db.scalars(
        select(inner.c.value).distinct().order_by(inner.c.value).limit(MAX_FILTER_VALUES)
    )
    return list(result.all())


async def filter_values(db: AsyncSession, project_id: uuid.UUID) -> FiltersOut:
    now = utcnow()
    since = now - FILTER_LOOKBACK
    recent_traces = and_(Trace.project_id == project_id, Trace.started_at >= since)
    week_traces = and_(Trace.project_id == project_id, Trace.started_at >= now - METADATA_LOOKBACK)
    return FiltersOut(
        environments=await _distinct_values(db, Trace.environment, recent_traces),
        releases=await _distinct_values(db, Trace.release, recent_traces),
        models=await _distinct_values(
            db, Span.model, Span.project_id == project_id, Span.started_at >= since
        ),
        tags=await _distinct_unnested(db, func.unnest(Trace.tags), recent_traces),
        score_names=await _distinct_values(
            db, Score.name, Score.project_id == project_id, Score.created_at >= since
        ),
        metadata_keys=await _distinct_unnested(
            db, func.jsonb_object_keys(Trace.metadata_), week_traces
        ),
        trace_names=await _distinct_values(db, Trace.name, week_traces),
    )

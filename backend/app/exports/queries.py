"""Reading traces for an export: filters, a capped count and keyset batches.

Every query is also bound to the project by row-level security (the caller binds the session);
the explicit `project_id` condition keeps index use obvious.
"""

import uuid
from collections import defaultdict
from datetime import datetime

from sqlalchemy import Select, and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import Span, Trace
from app.explorer.content_search import text_search
from app.explorer.metadata_filters import metadata_conditions
from app.exports.schemas import ExportFilters, SpanExportRow, TraceExportRow


def trace_conditions(project_id: uuid.UUID, filters: ExportFilters) -> list[ColumnElement[bool]]:
    """The conditions of the trace list for `filters`, with the same meaning for each filter."""
    conditions: list[ColumnElement[bool]] = [
        Trace.project_id == project_id,
        Trace.started_at >= filters.from_,
        Trace.started_at < filters.to,
    ]
    if filters.environment is not None:
        conditions.append(Trace.environment == filters.environment)
    if filters.release is not None:
        conditions.append(Trace.release == filters.release)
    if filters.user_id is not None:
        conditions.append(Trace.external_user_id == filters.user_id)
    if filters.session_id is not None:
        conditions.append(Trace.session_id == filters.session_id)
    if filters.tag is not None:
        conditions.append(Trace.tags.contains([filters.tag]))
    conditions += metadata_conditions(filters.metadata_key, filters.metadata_value)
    if filters.status == "error":
        conditions.append(Trace.error_count > 0)
    elif filters.status == "ok":
        conditions.append(Trace.error_count == 0)
    if filters.model is not None:
        conditions.append(
            exists().where(
                Span.project_id == Trace.project_id,
                Span.trace_id == Trace.trace_id,
                Span.model == filters.model,
            )
        )
    if filters.error_class is not None:
        conditions.append(
            exists().where(
                Span.project_id == Trace.project_id,
                Span.trace_id == Trace.trace_id,
                Span.error_class == filters.error_class.value,
            )
        )
    if filters.q:
        conditions.append(text_search(filters.q, project_id, filters.from_, filters.to))
    return conditions


async def count_traces_up_to(
    db: AsyncSession, project_id: uuid.UUID, filters: ExportFilters, cap: int
) -> int:
    """How many traces match, counting no further than `cap + 1`.

    A project can hold millions of traces in a 90-day window; the caller only needs to know
    whether the count passes `cap`, so the scan stops once it has seen one more than that.
    """
    limited = select(Trace.trace_id).where(*trace_conditions(project_id, filters)).limit(cap + 1)
    return int(await db.scalar(select(func.count()).select_from(limited.subquery())) or 0)


type Cursor = tuple[datetime, str]


def _batch_query(
    project_id: uuid.UUID, filters: ExportFilters, after: Cursor | None, size: int
) -> Select[Trace]:
    """Traces newest first, in the order of the trace list, after the cursor."""
    query = (
        select(Trace)
        .where(*trace_conditions(project_id, filters))
        .order_by(Trace.started_at.desc(), Trace.trace_id.desc())
        .limit(size)
    )
    if after is not None:
        started_at, trace_id = after
        query = query.where(
            or_(
                Trace.started_at < started_at,
                and_(Trace.started_at == started_at, Trace.trace_id < trace_id),
            )
        )
    return query


async def fetch_trace_batch(
    db: AsyncSession,
    project_id: uuid.UUID,
    filters: ExportFilters,
    *,
    after: Cursor | None,
    size: int,
    max_spans: int | None = None,
) -> list[TraceExportRow]:
    """Up to `size` traces after the cursor, with their spans when `max_spans` is given.

    With `max_spans`, the batch is cut after the trace that would take the summed
    `span_count` past it (always keeping at least one trace), so memory is bounded by the span
    payloads of `max_spans` spans. The cut-off traces are read again by the next batch, whose
    cursor is the last trace returned. A trace with more than `max_spans` spans comes back
    alone and without spans: the caller streams them with `fetch_span_chunk`.
    """
    traces = list((await db.scalars(_batch_query(project_id, filters, after, size))).all())
    if max_spans is None:
        return [_trace_row(trace, []) for trace in traces]

    kept: list[Trace] = []
    budget = 0
    for trace in traces:
        if kept and budget + trace.span_count > max_spans:
            break
        kept.append(trace)
        budget += trace.span_count

    spans_by_trace: dict[str, list[SpanExportRow]] = defaultdict(list)
    loadable = [trace.trace_id for trace in kept if trace.span_count <= max_spans]
    if loadable:
        spans = await db.scalars(
            select(Span)
            .where(Span.project_id == project_id, Span.trace_id.in_(loadable))
            .order_by(Span.trace_id, Span.started_at, Span.span_id)
        )
        for span in spans:
            spans_by_trace[span.trace_id].append(_span_row(span))
    return [_trace_row(trace, spans_by_trace.get(trace.trace_id, [])) for trace in kept]


async def fetch_span_chunk(
    db: AsyncSession,
    project_id: uuid.UUID,
    trace_id: str,
    *,
    after: Cursor | None,
    size: int,
) -> list[SpanExportRow]:
    """Up to `size` of one trace's spans in `(started_at, span_id)` order, after the cursor."""
    query = (
        select(Span)
        .where(Span.project_id == project_id, Span.trace_id == trace_id)
        .order_by(Span.started_at, Span.span_id)
        .limit(size)
    )
    if after is not None:
        started_at, span_id = after
        query = query.where(
            or_(
                Span.started_at > started_at,
                and_(Span.started_at == started_at, Span.span_id > span_id),
            )
        )
    return [_span_row(span) for span in (await db.scalars(query)).all()]


def _trace_row(trace: Trace, spans: list[SpanExportRow]) -> TraceExportRow:
    return TraceExportRow(
        trace_id=trace.trace_id,
        name=trace.name,
        started_at=trace.started_at,
        ended_at=trace.ended_at,
        duration_ms=(trace.ended_at - trace.started_at).total_seconds() * 1000,
        status="error" if trace.error_count > 0 else "ok",
        environment=trace.environment,
        release=trace.release,
        user_id=trace.external_user_id,
        session_id=trace.session_id,
        span_count=trace.span_count,
        error_count=trace.error_count,
        input_tokens=trace.input_tokens,
        output_tokens=trace.output_tokens,
        cost_usd=trace.cost_usd,
        has_unpriced=trace.has_unpriced,
        tags=list(trace.tags),
        metadata=dict(trace.metadata_),
        spans=spans,
    )


def _span_row(span: Span) -> SpanExportRow:
    return SpanExportRow(
        span_id=span.span_id,
        parent_span_id=span.parent_span_id,
        kind=span.kind.value,
        name=span.name,
        status=span.status.value,
        status_message=span.status_message,
        started_at=span.started_at,
        ended_at=span.ended_at,
        duration_ms=span.duration_ms,
        provider=span.provider,
        model=span.model,
        input_tokens=span.input_tokens,
        output_tokens=span.output_tokens,
        cached_tokens=span.cached_tokens,
        cache_write_tokens=span.cache_write_tokens,
        cache_write_1h_tokens=span.cache_write_1h_tokens,
        cost_usd=span.cost_usd,
        pricing_version=span.pricing_version,
        time_to_first_token_ms=span.time_to_first_token_ms,
        input=span.input,
        output=span.output,
        attributes=span.attributes,
        truncated=span.truncated,
        usage_rollup=span.usage_rollup,
    )

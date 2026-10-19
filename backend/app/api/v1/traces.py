"""Trace explorer: trace list and detail, sessions and filter values.

All queries run in a transaction bound to the project (row-level security)
and also filter on `project_id` explicitly, which keeps index usage obvious.
"""

import uuid
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, tuple_

from app.api.deps import DbSession, ReadAccess, require
from app.api.schemas import (
    FiltersOut,
    Page,
    SessionSummaryOut,
    SpanOut,
    StepOut,
    StepSpanOut,
    TraceCompareOut,
    TraceDetailOut,
    TraceSummaryOut,
)
from app.api.schemas.scores import ScoreSummaryOut
from app.api.schemas.traces import TraceTotalsDeltaOut
from app.api.window import Window
from app.core.errors import not_found
from app.core.pagination import DEFAULT_PAGE_SIZE, PageLimit, decode_cursor, encode_cursor
from app.core.permissions import Permission
from app.db.models import Trace
from app.explorer import trace_sort
from app.explorer.content_search import content_start, text_search
from app.explorer.filter_values import filter_values
from app.explorer.metadata_filters import check_metadata_filter
from app.explorer.score_filters import check_score_filters
from app.explorer.trace_compare import align, compare_spans, payload_changed
from app.explorer.trace_filters import apply_filters
from app.explorer.trace_queries import page_select, summary_select, trace_spans
from app.explorer.trace_sort import TraceSort
from app.ingest.error_class import ErrorClass
from app.ingest.metadata import MetadataKey
from app.scores.queries import ScoreSummary, latest_scores_by_name

router = APIRouter(prefix="/projects/{project_id}", tags=["traces"])

# Also readable by an API key with `traces:read`, for the project it belongs to.
ProjectReader = Annotated[ReadAccess, Depends(require(Permission.PROJECT_READ, allow_api_key=True))]
OptionalText = Annotated[str | None, Query(max_length=256)]
TraceIdQuery = Annotated[str, Query(min_length=1, max_length=256)]

MAX_ERROR_MESSAGE_LENGTH = 500


def _summary(
    trace: Trace,
    models: list[str] | None,
    error_message: str | None,
    error_class: str | None,
    scores: list[ScoreSummary],
) -> TraceSummaryOut:
    return TraceSummaryOut(
        trace_id=trace.trace_id,
        name=trace.name,
        environment=trace.environment,
        release=trace.release,
        external_user_id=trace.external_user_id,
        session_id=trace.session_id,
        tags=list(trace.tags),
        metadata=dict(trace.metadata_),
        started_at=trace.started_at,
        ended_at=trace.ended_at,
        duration_ms=(trace.ended_at - trace.started_at).total_seconds() * 1000,
        span_count=trace.span_count,
        error_count=trace.error_count,
        input_tokens=trace.input_tokens,
        output_tokens=trace.output_tokens,
        cost_usd=trace.cost_usd,
        has_unpriced=trace.has_unpriced,
        models=sorted(models or []),
        error_message=error_message[:MAX_ERROR_MESSAGE_LENGTH] if error_message else None,
        error_class=ErrorClass(error_class) if error_class else None,
        has_scores=trace.has_scores,
        retry_calls=trace.retry_calls,
        retry_cost_usd=trace.retry_cost_usd,
        scores=[ScoreSummaryOut(**score._asdict()) for score in scores],
    )


@router.get("/traces", response_model=Page[TraceSummaryOut])
async def list_traces(
    project_id: uuid.UUID,
    access: ProjectReader,
    db: DbSession,
    window: Window,
    name: OptionalText = None,
    environment: OptionalText = None,
    release: OptionalText = None,
    model: OptionalText = None,
    status: Literal["ok", "error"] | None = None,
    error_class: ErrorClass | None = None,
    prompt_version_id: uuid.UUID | None = None,
    score_name: OptionalText = None,
    score_min: Decimal | None = None,
    score_max: Decimal | None = None,
    score_value: OptionalText = None,
    has_scores: bool | None = None,
    has_retries: bool | None = None,
    user_id: OptionalText = None,
    session_id: OptionalText = None,
    tag: OptionalText = None,
    metadata_key: Annotated[MetadataKey | None, Query()] = None,
    metadata_value: OptionalText = None,
    q: Annotated[
        str | None,
        Query(
            max_length=256,
            description=(
                "Matches the trace name (substring, ignoring case), an exact trace id, or, with 3+ "
                "characters, a preview of the input and output of a span started in the window."
            ),
        ),
    ] = None,
    sort: TraceSort = "started_at",
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[TraceSummaryOut]:
    project = access.require_project()
    check_score_filters(score_name, score_min, score_max, score_value)
    check_metadata_filter(metadata_key, metadata_value)
    # The page is picked from the bare table first; the per-trace summary lookups then run for
    # that page only (`page_select`).
    page_query = select(Trace).where(
        Trace.project_id == project.id,
        Trace.started_at >= window.start,
        Trace.started_at < window.end,
    )
    page_query = apply_filters(
        page_query,
        name=name,
        environment=environment,
        release=release,
        user_id=user_id,
        session_id=session_id,
        tag=tag,
        metadata_key=metadata_key,
        metadata_value=metadata_value,
        status=status,
        model=model,
        error_class=error_class,
        prompt_version_id=prompt_version_id,
        score_name=score_name,
        score_min=score_min,
        score_max=score_max,
        score_value=score_value,
        has_scores=has_scores,
        has_retries=has_retries,
    )
    if q:
        content_from = await content_start(db, window.start, window.end)
        page_query = page_query.where(text_search(q, project.id, content_from, window.end))
    page_query = trace_sort.apply(page_query, sort=sort, cursor=cursor).limit(limit + 1)
    paged, query = page_select(page_query)
    query = query.order_by(*trace_sort.ordering(sort, paged))

    rows = (await db.execute(query)).all()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        next_cursor = trace_sort.next_cursor(page[-1][0], sort)
    scores = await latest_scores_by_name(db, project.id, [row[0].trace_id for row in page])
    return Page[TraceSummaryOut](
        items=[_summary(*row, scores.get(row[0].trace_id, [])) for row in page],
        next_cursor=next_cursor,
    )


@router.get("/traces/compare", response_model=TraceCompareOut)
async def compare_traces(
    project_id: uuid.UUID, a: TraceIdQuery, b: TraceIdQuery, access: ProjectReader, db: DbSession
) -> TraceCompareOut:
    """Two traces of this project, step by step: B's change against A.

    Registered before `/traces/{trace_id}`, which would otherwise take `compare` as an id. A
    missing trace is a 404 that does not say which one, as trace detail's.
    """
    project = access.require_project()
    a, b = a.lower(), b.lower()
    rows = (
        await db.execute(
            summary_select().where(Trace.project_id == project.id, Trace.trace_id.in_({a, b}))
        )
    ).all()
    found = {row[0].trace_id: row for row in rows}
    if a not in found or b not in found:
        raise not_found()
    scores = await latest_scores_by_name(db, project.id, list(found))
    summary_a = _summary(*found[a], scores.get(a, []))
    summary_b = _summary(*found[b], scores.get(b, []))
    spans_a, cut_a = await compare_spans(db, project.id, a)
    spans_b, cut_b = await compare_spans(db, project.id, b)

    unpriced = summary_a.has_unpriced or summary_b.has_unpriced
    totals = TraceTotalsDeltaOut(
        duration_ms=summary_b.duration_ms - summary_a.duration_ms,
        input_tokens=summary_b.input_tokens - summary_a.input_tokens,
        output_tokens=summary_b.output_tokens - summary_a.output_tokens,
        cost_usd=None if unpriced else _change(summary_a.cost_usd, summary_b.cost_usd),
        span_count=summary_b.span_count - summary_a.span_count,
        error_count=summary_b.error_count - summary_a.error_count,
    )
    steps = [
        StepOut(
            name=name,
            occurrence=occurrence,
            a=None if span_a is None else StepSpanOut.model_validate(span_a, from_attributes=True),
            b=None if span_b is None else StepSpanOut.model_validate(span_b, from_attributes=True),
            duration_ms=_change(span_a and span_a.duration_ms, span_b and span_b.duration_ms),
            input_tokens=_change(span_a and span_a.input_tokens, span_b and span_b.input_tokens),
            output_tokens=_change(span_a and span_a.output_tokens, span_b and span_b.output_tokens),
            cost_usd=_change(span_a and span_a.cost_usd, span_b and span_b.cost_usd),
            input_changed=payload_changed(span_a, span_b, "input_hash"),
            output_changed=payload_changed(span_a, span_b, "output_hash"),
        )
        for (name, occurrence), span_a, span_b in align(spans_a, spans_b)
    ]
    return TraceCompareOut(
        a=summary_a, b=summary_b, totals=totals, steps=steps, truncated=cut_a or cut_b
    )


def _change[T: (int, float, Decimal)](before: T | None, after: T | None) -> T | None:
    """`after - before`; None when either is unknown."""
    return None if before is None or after is None else after - before


@router.get("/traces/{trace_id}", response_model=TraceDetailOut)
async def get_trace(
    project_id: uuid.UUID, trace_id: str, access: ProjectReader, db: DbSession
) -> TraceDetailOut:
    project = access.require_project()
    row = (
        await db.execute(
            summary_select().where(
                Trace.project_id == project.id, Trace.trace_id == trace_id.lower()
            )
        )
    ).one_or_none()
    if row is None:
        raise not_found()
    scores = await latest_scores_by_name(db, project.id, [row[0].trace_id])
    summary = _summary(*row, scores.get(row[0].trace_id, []))
    spans = await trace_spans(db, project.id, summary.trace_id)
    return TraceDetailOut(
        **summary.model_dump(),
        spans=[SpanOut.model_validate(span, from_attributes=True) for span in spans],
    )


@router.get("/sessions", response_model=Page[SessionSummaryOut])
async def list_sessions(
    project_id: uuid.UUID,
    access: ProjectReader,
    db: DbSession,
    window: Window,
    environment: OptionalText = None,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[SessionSummaryOut]:
    project = access.require_project()
    last_at = func.max(Trace.ended_at)
    query = (
        select(
            Trace.session_id,
            func.count().label("trace_count"),
            func.min(Trace.started_at).label("first_at"),
            last_at.label("last_at"),
            func.sum(Trace.cost_usd).label("cost_usd"),
            func.sum(Trace.error_count).label("error_count"),
        )
        .where(
            Trace.project_id == project.id,
            Trace.session_id.is_not(None),
            Trace.started_at >= window.start,
            Trace.started_at < window.end,
        )
        .group_by(Trace.session_id)
        .order_by(last_at.desc(), Trace.session_id.desc())
        .limit(limit + 1)
    )
    if environment is not None:
        # Applied before the grouping: counts, cost and errors cover this environment's traces only.
        query = query.where(Trace.environment == environment)
    if cursor:
        cursor_last_at, cursor_session_id = decode_cursor(cursor)
        query = query.having(
            tuple_(last_at, Trace.session_id) < tuple_(cursor_last_at, cursor_session_id)
        )

    rows = (await db.execute(query)).all()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1]
        next_cursor = encode_cursor(last.last_at, last.session_id)
    items = [
        SessionSummaryOut(
            session_id=row.session_id,
            trace_count=row.trace_count,
            first_at=row.first_at,
            last_at=row.last_at,
            cost_usd=row.cost_usd,
            error_count=row.error_count,
        )
        for row in page
    ]
    return Page[SessionSummaryOut](items=items, next_cursor=next_cursor)


@router.get("/filters", response_model=FiltersOut)
async def list_filter_values(
    project_id: uuid.UUID, access: ProjectReader, db: DbSession
) -> FiltersOut:
    return await filter_values(db, access.require_project().id)

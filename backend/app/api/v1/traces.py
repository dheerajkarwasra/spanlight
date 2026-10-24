"""Trace explorer: trace list, histogram, compare and detail, and filter values.

Sessions are in `app.api.v1.sessions`.

All queries run in a transaction bound to the project (row-level security)
and also filter on `project_id` explicitly, which keeps index usage obvious.
"""

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.api.deps import DbSession, ReadAccess, require
from app.api.schemas import (
    FiltersOut,
    Page,
    SpanOut,
    StepOut,
    StepSpanOut,
    TraceCompareOut,
    TraceDetailOut,
    TraceSummaryOut,
)
from app.api.schemas.scores import ScoreSummaryOut
from app.api.schemas.traces import TraceHistogramOut, TraceTotalsDeltaOut
from app.api.trace_filters import TraceFilters
from app.api.window import Window
from app.core.errors import not_found
from app.core.pagination import DEFAULT_PAGE_SIZE, PageLimit
from app.core.permissions import Permission
from app.db.models import Trace
from app.explorer import trace_sort
from app.explorer.filter_values import filter_values
from app.explorer.trace_compare import align, compare_spans, payload_changed
from app.explorer.trace_histogram import trace_histogram
from app.explorer.trace_previews import trace_previews
from app.explorer.trace_queries import SummaryDetails, summary_details, trace_span, trace_spans
from app.explorer.trace_sort import SortOrder, TraceSort
from app.ingest.error_class import ErrorClass
from app.scores.queries import ScoreSummary, latest_scores_by_name

router = APIRouter(prefix="/projects/{project_id}", tags=["traces"])

# Also readable by an API key with `traces:read`, for the project it belongs to.
ProjectReader = Annotated[ReadAccess, Depends(require(Permission.PROJECT_READ, allow_api_key=True))]
TraceIdQuery = Annotated[str, Query(min_length=1, max_length=256)]

MAX_ERROR_MESSAGE_LENGTH = 500
NO_DETAILS = SummaryDetails(None, None, None)


def trace_summary(
    trace: Trace,
    models: list[str] | None,
    error_message: str | None,
    error_class: str | None,
    scores: list[ScoreSummary],
    previews: tuple[str | None, str | None] = (None, None),
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
        input_preview=previews[0],
        output_preview=previews[1],
    )


@router.get(
    "/traces",
    response_model=Page[TraceSummaryOut],
    responses={
        422: {
            "description": "A parameter is invalid (`VALIDATION_ERROR`), or a `q` search matched "
            "too much to finish (`SEARCH_TOO_BROAD`)."
        }
    },
)
async def list_traces(
    project_id: uuid.UUID,
    access: ProjectReader,
    db: DbSession,
    window: Window,
    filters: TraceFilters,
    sort: TraceSort = "started_at",
    order: SortOrder = "desc",
    preview: Annotated[
        bool,
        Query(description="Fill `input_preview` and `output_preview` (null otherwise)."),
    ] = False,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[TraceSummaryOut]:
    project = access.require_project()
    page_query = await filters.narrow(db, select(Trace), project.id, window)
    page_query = trace_sort.apply(page_query, sort=sort, order=order, cursor=cursor).limit(
        limit + 1
    )
    with filters.too_broad_on_timeout():
        rows = (await db.scalars(page_query)).all()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        next_cursor = trace_sort.next_cursor(page[-1], sort)
    trace_ids = [trace.trace_id for trace in page]
    details = await summary_details(db, project.id, trace_ids)
    scores = await latest_scores_by_name(db, project.id, trace_ids)
    previews = await trace_previews(db, project.id, trace_ids) if preview else {}
    return Page[TraceSummaryOut](
        items=[
            trace_summary(
                trace,
                *details.get(trace.trace_id, NO_DETAILS),
                scores.get(trace.trace_id, []),
                previews.get(trace.trace_id, (None, None)),
            )
            for trace in page
        ],
        next_cursor=next_cursor,
    )


@router.get(
    "/traces/histogram",
    response_model=TraceHistogramOut,
    responses={422: {"description": "As for `GET /traces`."}},
)
async def get_trace_histogram(
    project_id: uuid.UUID,
    access: ProjectReader,
    db: DbSession,
    window: Window,
    filters: TraceFilters,
) -> TraceHistogramOut:
    """Trace and error counts over time for the trace list's filters. Registered before
    `/traces/{trace_id}`, which would otherwise take `histogram` as an id."""
    return await trace_histogram(db, access.require_project().id, window, filters)


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
    traces = (
        await db.scalars(
            select(Trace).where(Trace.project_id == project.id, Trace.trace_id.in_({a, b}))
        )
    ).all()
    found = {trace.trace_id: trace for trace in traces}
    if a not in found or b not in found:
        raise not_found()
    details = await summary_details(db, project.id, list(found))
    scores = await latest_scores_by_name(db, project.id, list(found))
    summary_a = trace_summary(found[a], *details.get(a, NO_DETAILS), scores.get(a, []))
    summary_b = trace_summary(found[b], *details.get(b, NO_DETAILS), scores.get(b, []))
    spans_a, cut_a = await compare_spans(db, project.id, a)
    spans_b, cut_b = await compare_spans(db, project.id, b)

    unpriced = summary_a.has_unpriced or summary_b.has_unpriced
    totals = TraceTotalsDeltaOut(
        duration_ms=summary_b.duration_ms - summary_a.duration_ms,
        input_tokens=_change(summary_a.input_tokens, summary_b.input_tokens),
        output_tokens=_change(summary_a.output_tokens, summary_b.output_tokens),
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
    trace = await db.scalar(
        select(Trace).where(Trace.project_id == project.id, Trace.trace_id == trace_id.lower())
    )
    if trace is None:
        raise not_found()
    details = await summary_details(db, project.id, [trace.trace_id])
    scores = await latest_scores_by_name(db, project.id, [trace.trace_id])
    summary = trace_summary(
        trace, *details.get(trace.trace_id, NO_DETAILS), scores.get(trace.trace_id, [])
    )
    spans = await trace_spans(db, project.id, summary.trace_id)
    return TraceDetailOut(
        **summary.model_dump(),
        spans=[
            SpanOut.model_validate(span, from_attributes=True).model_copy(
                update={"payload_omitted": omitted}
            )
            for span, omitted in spans
        ],
    )


@router.get("/traces/{trace_id}/spans/{span_id}", response_model=SpanOut)
async def get_span(
    project_id: uuid.UUID, trace_id: str, span_id: str, access: ProjectReader, db: DbSession
) -> SpanOut:
    """One span with its payloads: what trace detail leaves out past its payload budget."""
    project = access.require_project()
    span = await trace_span(db, project.id, trace_id.lower(), span_id.lower())
    if span is None:
        raise not_found()
    return SpanOut.model_validate(span, from_attributes=True)


@router.get("/filters", response_model=FiltersOut)
async def list_filter_values(
    project_id: uuid.UUID, access: ProjectReader, db: DbSession
) -> FiltersOut:
    return await filter_values(db, access.require_project().id)

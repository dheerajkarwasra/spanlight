"""SQL for the trace list and trace detail (`app.api.v1.traces`).

Every statement filters on `project_id` explicitly as well as through row-level security, which
keeps index usage obvious. The summary lookups are one statement built at import: building them
per call (ORM aliases over the page subquery) was about a third of the trace list's api time.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, NamedTuple

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Select,
    String,
    Text,
    and_,
    bindparam,
    case,
    cast,
    exists,
    func,
    literal,
    or_,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, defer
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.sql.elements import ColumnElement

from app.core.errors import ProblemError
from app.db.models import Span, SpanKind, Trace
from app.ingest.normalize import LEGACY_PAYLOAD_BYTES

# The stored content bytes one trace-detail response carries: each span's input and output as
# stored (`payload_bytes`, the legacy fallback for rows without it) plus its attributes' JSON text,
# summed in start order. A trace's content together is unbounded; from the first span past this
# on, spans come back without attributes, input or output (`attributes_omitted`,
# `payload_omitted`) and are read one at a time (`GET .../spans/{span_id}`). One span (at most
# 2 MiB of payloads and 64 KiB of attributes) always fits.
TRACE_PAYLOAD_BUDGET_BYTES = 8 * 1024 * 1024


def span_content_too_large() -> ProblemError:
    """A caller that did not ask for `span_content=bounded` never gets withheld attributes: an
    older client would read the `{}` placeholder as a span without attributes."""
    return ProblemError(
        422,
        "SPAN_CONTENT_TOO_LARGE",
        "This response requires bounded span content. Reload the dashboard, or update the API "
        "request to include span_content=bounded and handle payload_omitted and "
        "attributes_omitted.",
    )


class ProjectedSpan(NamedTuple):
    """A span as trace detail, session turns and the span route return it."""

    span: Span
    payload_omitted: bool
    """Non-null input or output was withheld. False when both are stored null (known absent)."""
    attributes_omitted: bool
    """The attributes were withheld; `span.attributes` is a `{}` placeholder."""
    retry_eligible: bool
    """`RETRY_ELIGIBLE`, read from the stored attributes whether or not they were withheld."""


# Each span's stored content weight (see `TRACE_PAYLOAD_BUDGET_BYTES`).
CONTENT_BYTES = func.coalesce(Span.payload_bytes, LEGACY_PAYLOAD_BYTES) + func.octet_length(
    cast(Span.attributes, Text)
)


def _is_content(payload: InstrumentedAttribute[Any]) -> ColumnElement[bool]:
    """A stored payload other than SQL NULL or JSON null; `""`, `{}` and `[]` are content."""
    return and_(payload.isnot(None), func.jsonb_typeof(payload) != literal("null"))


# Whether the span has any input or output content, read from the stored values (never from
# `payload_bytes`, which legacy rows lack).
HAS_PAYLOAD = or_(_is_content(Span.input), _is_content(Span.output))

# The span can be a retry attempt: the predicate `traces.retry_calls` counts by
# (`_RECOMPUTE_ROLLUPS` in `app.ingest.store`). The cache check is exact string equality and the
# fault check key existence, whatever the value.
RETRY_ELIGIBLE = and_(
    Span.kind == SpanKind.LLM,
    ~Span.usage_rollup,
    Span.request_hash.isnot(None),
    Span.attributes["spanlight.cache"].astext.is_distinct_from("hit"),
    ~Span.attributes.has_key("spanlight.fault.scenario"),
)


class SummaryDetails(NamedTuple):
    models: list[str] | None
    error_message: str | None
    error_class: str | None
    root_received: bool | None


# Each trace's distinct models, its earliest failed span's status message and error class
# (ties broken by span id), and whether a span without a parent (the root) was stored. The
# earliest failed span is one `LEFT JOIN LATERAL … LIMIT 1` on the spans primary key
# (project_id, trace_id, …), so the message and the class are always the same span's; the root
# check is an `EXISTS` probe on the same key. One array parameter, not one per id: psycopg caches
# the parsed query only up to 50 parameters, and a page is 50 traces.
SUMMARY_DETAILS = text(
    """
    SELECT t.trace_id,
           (SELECT array_agg(DISTINCT s.model) FILTER (WHERE s.model IS NOT NULL)
              FROM spans AS s
             WHERE s.project_id = :project_id AND s.trace_id = t.trace_id) AS models,
           f.status_message,
           f.error_class,
           EXISTS (SELECT 1
                     FROM spans AS s
                    WHERE s.project_id = :project_id AND s.trace_id = t.trace_id
                      AND s.parent_span_id IS NULL) AS root_received
    FROM unnest(:trace_ids) AS t(trace_id)
    LEFT JOIN LATERAL (
        SELECT s.status_message, s.error_class
          FROM spans AS s
         WHERE s.project_id = :project_id AND s.trace_id = t.trace_id AND s.status = 'error'
         ORDER BY s.started_at, s.span_id
         LIMIT 1
    ) AS f ON true
    """
).bindparams(bindparam("trace_ids", type_=ARRAY(String)))


async def summary_details(
    db: AsyncSession, project_id: uuid.UUID, trace_ids: Sequence[str]
) -> dict[str, SummaryDetails]:
    """`SUMMARY_DETAILS` for the given traces, by trace id. One query; none for no ids."""
    if not trace_ids:
        return {}
    rows = await db.execute(SUMMARY_DETAILS, {"project_id": project_id, "trace_ids": trace_ids})
    return {trace_id: SummaryDetails(*details) for trace_id, *details in rows}


def has_span(
    condition: ColumnElement[bool], started_from: datetime | None = None
) -> ColumnElement[bool]:
    """The trace has at least one span matching `condition`.

    `started_from` is the lower bound of the outer query's `Trace.started_at`. A trace starts at
    its earliest stored span (the ingest recompute), so its spans start no earlier: the bound
    selects the same traces and lets the span lookup read an index range from the window start
    instead of every matching span the project ever stored.
    """
    bound = [] if started_from is None else [Span.started_at >= started_from]
    return exists().where(
        Span.project_id == Trace.project_id, Span.trace_id == Trace.trace_id, condition, *bound
    )


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _bounded(order: Sequence[Any]) -> Select[Any]:
    """Spans with their content while the running sum of `CONTENT_BYTES` in `order` stays within
    `TRACE_PAYLOAD_BUDGET_BYTES`, and without it from the first span past it on: the withheld
    JSON is never read out of the database (`defer` plus CASE). Presence and retry eligibility
    are read from the stored values before the CASE, so the serializer never infers them from
    the withheld nulls."""
    inline = func.sum(CONTENT_BYTES).over(order_by=order) <= TRACE_PAYLOAD_BUDGET_BYTES
    return select(
        Span,
        inline,
        case((inline, Span.input)),
        case((inline, Span.output)),
        case((inline, Span.attributes)),
        HAS_PAYLOAD,
        RETRY_ELIGIBLE,
    ).options(defer(Span.input), defer(Span.output), defer(Span.attributes))


def _projected(row: Sequence[Any]) -> ProjectedSpan:
    span, inlined, stored_input, stored_output, stored_attributes, has_payload, eligible = row
    set_committed_value(span, "input", stored_input)
    set_committed_value(span, "output", stored_output)
    set_committed_value(span, "attributes", stored_attributes if inlined else {})
    return ProjectedSpan(span, not inlined and has_payload, not inlined, eligible)


async def trace_spans(
    db: AsyncSession, project_id: uuid.UUID, trace_id: str
) -> list[ProjectedSpan]:
    """One trace's spans in start order, bounded by `TRACE_PAYLOAD_BUDGET_BYTES`."""
    order = (Span.started_at, Span.span_id)
    rows = await db.execute(
        _bounded(order)
        .where(Span.project_id == project_id, Span.trace_id == trace_id)
        .order_by(*order)
    )
    return [_projected(row) for row in rows]


async def trace_span(
    db: AsyncSession, project_id: uuid.UUID, trace_id: str, span_id: str
) -> ProjectedSpan | None:
    """One span with all of its content, whatever its trace's size."""
    row = (
        await db.execute(
            select(Span, RETRY_ELIGIBLE).where(
                Span.project_id == project_id, Span.trace_id == trace_id, Span.span_id == span_id
            )
        )
    ).first()
    return None if row is None else ProjectedSpan(row[0], False, False, row[1])


# The spans a session turn is read from (`SessionTurnOut`), for a page of traces: per trace the
# root (no parent, or a parent not stored, as the conversation view finds it), the first llm span
# with an input, the last llm span with an output and the earliest failed span; UNION drops a span
# picked twice. Each pick is a `LIMIT 1` over the trace's spans by primary key prefix, ties broken
# by span id as everywhere else; `ord` is the trace's place on the page. `OFFSET 0` keeps the
# planner from inlining the picks into the join with `spans`, where it ran the picks once per span
# of the trace instead of once per trace.
TURN_SPAN_PICKS = (
    text(
        """
        SELECT t.trace_id, p.span_id, t.ord
        FROM unnest(:trace_ids) WITH ORDINALITY AS t(trace_id, ord)
        CROSS JOIN LATERAL (
            (SELECT s.span_id FROM spans AS s
              WHERE s.project_id = :project_id AND s.trace_id = t.trace_id
                AND (s.parent_span_id IS NULL OR NOT EXISTS (
                    SELECT 1 FROM spans AS parent
                     WHERE parent.project_id = s.project_id AND parent.trace_id = s.trace_id
                       AND parent.span_id = s.parent_span_id))
              ORDER BY s.started_at, s.span_id LIMIT 1)
            UNION
            (SELECT s.span_id FROM spans AS s
              WHERE s.project_id = :project_id AND s.trace_id = t.trace_id
                AND s.kind = 'llm' AND s.input IS NOT NULL
              ORDER BY s.started_at, s.span_id LIMIT 1)
            UNION
            (SELECT s.span_id FROM spans AS s
              WHERE s.project_id = :project_id AND s.trace_id = t.trace_id
                AND s.kind = 'llm' AND s.output IS NOT NULL
              ORDER BY s.started_at DESC, s.span_id DESC LIMIT 1)
            UNION
            (SELECT s.span_id FROM spans AS s
              WHERE s.project_id = :project_id AND s.trace_id = t.trace_id AND s.status = 'error'
              ORDER BY s.started_at, s.span_id LIMIT 1)
        ) AS p
        OFFSET 0
        """
    )
    .bindparams(bindparam("trace_ids", type_=ARRAY(String)))
    .columns(trace_id=String, span_id=String, ord=BigInteger)
)


async def turn_spans(
    db: AsyncSession, project_id: uuid.UUID, trace_ids: Sequence[str]
) -> dict[str, list[ProjectedSpan]]:
    """`TURN_SPAN_PICKS` for a page of traces, by trace id. One query; none for no ids.

    The page shares one `TRACE_PAYLOAD_BUDGET_BYTES`, spent in page order and then start order,
    as `trace_spans` spends it within one trace.
    """
    if not trace_ids:
        return {}
    picks = TURN_SPAN_PICKS.subquery("picks")
    order = (picks.c.ord, Span.started_at, Span.span_id)
    rows = await db.execute(
        _bounded(order)
        .join(
            picks,
            and_(
                Span.project_id == project_id,
                Span.trace_id == picks.c.trace_id,
                Span.span_id == picks.c.span_id,
            ),
        )
        .order_by(*order),
        {"project_id": project_id, "trace_ids": list(trace_ids)},
    )
    spans: dict[str, list[ProjectedSpan]] = {}
    for row in rows:
        projected = _projected(row)
        spans.setdefault(projected.span.trace_id, []).append(projected)
    return spans

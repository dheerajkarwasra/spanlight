"""SQL for the trace list and trace detail (`app.api.v1.traces`).

Every statement filters on `project_id` explicitly as well as through row-level security, which
keeps index usage obvious. The summary lookups are one statement built at import: building them
per call (ORM aliases over the page subquery) was about a third of the trace list's api time.
"""

import uuid
from collections.abc import Sequence
from typing import NamedTuple

from sqlalchemy import ARRAY, BigInteger, String, and_, bindparam, case, exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import Span, Trace
from app.ingest.normalize import LEGACY_PAYLOAD_BYTES

# The stored payload bytes one trace-detail response carries. With payloads up to 1 MiB a side,
# a trace's payloads together are unbounded; past this, spans come back without them and are
# read one at a time (`GET .../spans/{span_id}`). One span (at most 2 MiB) always fits.
TRACE_PAYLOAD_BUDGET_BYTES = 8 * 1024 * 1024


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


def has_span(condition: ColumnElement[bool]) -> ColumnElement[bool]:
    """The trace has at least one span matching `condition`."""
    return exists().where(
        Span.project_id == Trace.project_id, Span.trace_id == Trace.trace_id, condition
    )


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def trace_spans(
    db: AsyncSession, project_id: uuid.UUID, trace_id: str
) -> list[tuple[Span, bool]]:
    """One trace's spans in start order, each with whether its payloads were left out.

    Spans keep their `input` and `output` while the running sum of their stored payload bytes
    stays within `TRACE_PAYLOAD_BUDGET_BYTES`; the later spans' payloads are never read (the
    window sum needs only `payload_bytes`), and come back as None.
    """
    order = (Span.started_at, Span.span_id)
    running = func.sum(func.coalesce(Span.payload_bytes, LEGACY_PAYLOAD_BYTES)).over(order_by=order)
    inline = running <= TRACE_PAYLOAD_BUDGET_BYTES
    rows = await db.execute(
        select(Span, inline, case((inline, Span.input)), case((inline, Span.output)))
        .options(defer(Span.input), defer(Span.output))
        .where(Span.project_id == project_id, Span.trace_id == trace_id)
        .order_by(*order)
    )
    spans: list[tuple[Span, bool]] = []
    for span, inlined, stored_input, stored_output in rows:
        set_committed_value(span, "input", stored_input)
        set_committed_value(span, "output", stored_output)
        spans.append((span, not inlined))
    return spans


async def trace_span(
    db: AsyncSession, project_id: uuid.UUID, trace_id: str, span_id: str
) -> Span | None:
    """One span with its payloads, whatever its trace's size."""
    return await db.get(Span, (project_id, trace_id, span_id))


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
) -> dict[str, list[tuple[Span, bool]]]:
    """`TURN_SPAN_PICKS` for a page of traces, by trace id, each span with whether its payloads
    were left out. One query; none for no ids.

    The page shares one `TRACE_PAYLOAD_BUDGET_BYTES`, spent in page order and then start order,
    as `trace_spans` spends it within one trace.
    """
    if not trace_ids:
        return {}
    picks = TURN_SPAN_PICKS.subquery("picks")
    order = (picks.c.ord, Span.started_at, Span.span_id)
    running = func.sum(func.coalesce(Span.payload_bytes, LEGACY_PAYLOAD_BYTES)).over(order_by=order)
    inline = running <= TRACE_PAYLOAD_BUDGET_BYTES
    rows = await db.execute(
        select(Span, inline, case((inline, Span.input)), case((inline, Span.output)))
        .options(defer(Span.input), defer(Span.output))
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
    spans: dict[str, list[tuple[Span, bool]]] = {}
    for span, inlined, stored_input, stored_output in rows:
        set_committed_value(span, "input", stored_input)
        set_committed_value(span, "output", stored_output)
        spans.setdefault(span.trace_id, []).append((span, not inlined))
    return spans

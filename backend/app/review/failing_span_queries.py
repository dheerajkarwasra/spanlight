"""The raw read behind the failing-span rollup: each reviewed trace of the window with the span
its latest `failure_category` label names.

The traces are the window's (`traces_project_started_idx`). A trace counts once, under its latest
`failure_category` score with a string value (ranked by `created_at` then `id`, the release
read's rule), whether or not that score names a span. `reviewed` is how many such traces there
are; the groups keep only labels that name a span and are not `none`, joined to `spans` on the
primary key for the span's name and kind. A label whose span was never stored or was deleted by
retention joins as `span_name`/`span_kind` `NULL` (unknown, not dropped).

The query runs in the caller's session and relies on its project binding for row-level security;
the explicit `project_id` filter is a second guard, not the only one.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.window import TimeWindow
from app.scores.failure_category import FAILURE_CATEGORY_SCORE, NONE_CATEGORY

MAX_GROUPS = 200

_FAILING_SPANS = text(
    """
    WITH scoped AS (
        SELECT t.trace_id
        FROM traces AS t
        WHERE t.project_id = :project_id
          AND t.started_at >= :start
          AND t.started_at < :end
    ),
    latest AS (
        SELECT DISTINCT ON (s.trace_id) s.trace_id, s.span_id, s.string_value AS label
        FROM scoped AS c
        JOIN scores AS s ON s.project_id = :project_id AND s.trace_id = c.trace_id
        WHERE s.name = :name
          AND s.string_value IS NOT NULL
        ORDER BY s.trace_id, s.created_at DESC, s.id DESC
    )
    SELECT r.reviewed, g.span_name, g.span_kind, g.label, g.n
    FROM (SELECT count(*) AS reviewed FROM latest) AS r
    LEFT JOIN LATERAL (
        SELECT sp.name AS span_name, sp.kind::text AS span_kind, l.label, count(*) AS n
        FROM latest AS l
        LEFT JOIN spans AS sp
               ON sp.project_id = :project_id
              AND sp.trace_id = l.trace_id
              AND sp.span_id = l.span_id
        WHERE l.span_id IS NOT NULL
          AND l.label <> :none
        GROUP BY sp.name, sp.kind, l.label
        ORDER BY n DESC, span_name NULLS LAST, span_kind NULLS LAST, l.label
        LIMIT :limit
    ) AS g ON true
    """
)


@dataclass(frozen=True, slots=True)
class FailingSpanCount:
    """Reviewed traces whose latest label is `label` on a span named `span_name` of `span_kind`
    (both `None` when the span is not stored)."""

    span_name: str | None
    span_kind: str | None
    label: str
    count: int


async def read_failing_spans(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow
) -> tuple[int, list[FailingSpanCount]]:
    """The window's reviewed traces and up to `MAX_GROUPS + 1` groups, the largest first."""
    params = {
        "project_id": project_id,
        "start": window.start,
        "end": window.end,
        "name": FAILURE_CATEGORY_SCORE,
        "none": NONE_CATEGORY,
        "limit": MAX_GROUPS + 1,
    }
    rows = (await db.execute(_FAILING_SPANS, params)).all()
    groups = [
        FailingSpanCount(row.span_name, row.span_kind, row.label, int(row.n))
        for row in rows
        if row.label is not None
    ]
    return int(rows[0].reviewed), groups

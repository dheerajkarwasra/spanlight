"""The raw read behind the failure-category rates: each trace of the window with its latest
`failure_category` label.

The traces are the window's (`traces_project_started_idx`), releases included as `NULL`, so a
trace that carries no release is counted under the `release: null` entry. The labels are the
project's `failure_category` scores with a string value (`scores_project_name_created_idx`),
ranked per trace by `created_at` then `id`, whatever their `span_id`; only the first one per
trace joins. Traces without a label join as `label IS NULL`, which is how a release nobody
reviewed is still listed.

The query runs in the caller's session and relies on its project binding for row-level security;
the explicit `project_id` filter is a second guard, not the only one.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.window import TimeWindow
from app.releases.failure_categories import LabelCount, ReviewedCount
from app.releases.queries import _params, _statement
from app.scores.failure_category import FAILURE_CATEGORY_SCORE

_LATEST_LABELS = """
    WITH scoped AS (
        SELECT t.trace_id, t.release
        FROM traces AS t
        WHERE t.project_id = :project_id
          AND t.started_at >= :start
          AND t.started_at < :end
          {environment}
    ),
    latest AS (
        SELECT s.trace_id, s.string_value AS label,
               row_number() OVER (PARTITION BY s.trace_id ORDER BY s.created_at DESC, s.id DESC)
                   AS rn
        FROM scores AS s
        WHERE s.project_id = :project_id
          AND s.name = :name
          AND s.string_value IS NOT NULL
    )
    SELECT c.release, l.label, count(*) AS n
    FROM scoped AS c
    LEFT JOIN latest AS l ON l.trace_id = c.trace_id AND l.rn = 1
    GROUP BY c.release, l.label
"""


async def read_latest_failure_labels(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow, environment: str | None
) -> tuple[list[ReviewedCount], list[LabelCount]]:
    """Per release of the window: how many traces carry a failure category, and how many carry
    each label. Every release with a trace in the window gets a `ReviewedCount`, reviewed or
    not; `None` is the traces without a release."""
    statement = _statement(_LATEST_LABELS, environment=environment)
    params = _params(project_id, window, environment, name=FAILURE_CATEGORY_SCORE)
    rows = (await db.execute(statement, params)).all()
    reviewed: dict[str | None, int] = {}
    counts: list[LabelCount] = []
    for row in rows:
        reviewed.setdefault(row.release, 0)
        if row.label is not None:
            reviewed[row.release] += int(row.n)
            counts.append(LabelCount(row.release, row.label, int(row.n)))
    return [ReviewedCount(release, n) for release, n in reviewed.items()], counts

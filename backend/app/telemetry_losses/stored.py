"""How many spans a project stored in a window: the numerator of telemetry completeness.

Read like the metrics are (``app.alerts.metrics.plan_window``): whole hours before the split from
``span_rollups_hourly`` (spans of every kind), the rest from raw spans, so the count is exact.

The queries run in the caller's session and rely on its project binding for row-level security;
the explicit ``project_id`` filter is a second guard, not the only one.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.metrics import plan_window, rollup_split
from app.api.window import TimeWindow

# numeric: a sum of bigints may exceed one.
_ROLLUP_SPANS = text(
    """
    SELECT coalesce(sum(span_count), 0) AS spans
    FROM span_rollups_hourly
    WHERE project_id = :project_id AND bucket_start >= :start AND bucket_start < :end
    """
)

_RAW_SPANS = text(
    """
    SELECT count(*) AS spans
    FROM unnest(CAST(:starts AS timestamptz[]), CAST(:ends AS timestamptz[]))
         AS w(start_at, end_at)
    JOIN spans AS s
      ON s.project_id = :project_id
     AND s.started_at >= w.start_at
     AND s.started_at < w.end_at
    """
).bindparams(
    bindparam("starts", type_=ARRAY(DateTime(timezone=True))),
    bindparam("ends", type_=ARRAY(DateTime(timezone=True))),
)


async def read_stored_spans(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow, now: datetime
) -> int:
    """Spans of every kind that started in ``window``."""
    plan = plan_window(window, rollup_split(now))
    stored = 0
    if plan.rollup is not None:
        start, end = plan.rollup
        params = {"project_id": project_id, "start": start, "end": end}
        stored += int((await db.execute(_ROLLUP_SPANS, params)).scalar_one())
    ranges = [r for r in plan.raw if r.start < r.end]
    if ranges:
        params_raw = {
            "project_id": project_id,
            "starts": [r.start for r in ranges],
            "ends": [r.end for r in ranges],
        }
        stored += int((await db.execute(_RAW_SPANS, params_raw)).scalar_one())
    return stored

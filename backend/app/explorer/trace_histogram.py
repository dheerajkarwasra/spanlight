"""Trace and error counts over time, for exactly the traces the trace list shows."""

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import (
    DateTime,
    Interval,
    String,
    bindparam,
    func,
    literal_column,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnClause, ColumnElement, TextClause

from app.api.schemas.traces import TraceBucketOut, TraceHistogramOut
from app.api.trace_filters import TraceListFilters
from app.api.window import TimeWindow
from app.db.models import Trace
from app.rollups.compute import ceil_hour, floor_hour

HOURLY_UP_TO = timedelta(hours=48)
# Any UTC midnight: bins of whole hours or days from it start where `date_trunc` in UTC would.
# Written as literals, not bind parameters: the planner uses the `traces_started_day_bin`
# statistics (migration 1017) only for this exact expression, and without them it expects one
# group per few traces and sorts the whole window.
_ORIGIN: ColumnClause[datetime] = literal_column(
    "timestamptz '2000-01-01 00:00:00+00'", DateTime(timezone=True)
)
_DAY: ColumnClause[timedelta] = literal_column("interval '1 day'", Interval())
_HOUR: ColumnClause[timedelta] = literal_column("interval '1 hour'", Interval())


# The same counts without reading every trace of the window: `trace_rollups_hourly` holds, per
# start hour and environment, exactly `count(*)` and `count(*) FILTER (WHERE error_count > 0)`.
# An hour's rows equal its traces when the hour is closed (before `next_closed_hour`), verified
# (from `aggregate_history_unverified_before` on), after the oldest hour rollup retention has
# left, and neither dirty for the hourly rollup nor incomplete: every writer that changes a
# trace marks its hours dirty in its own transaction, and trace retention records the hours it
# took rows from. Those hours are read from the rollup; the window's partial first and last
# hours, the hours not yet closed and every hour that fails a condition are counted from
# `traces`, in the same statement and so the same snapshot. The result is the raw histogram's,
# row for row. `{bucket}` is written with the literals of `_ORIGIN`, `_DAY` and `_HOUR` for the
# reason given there.
_ROLLED = """
    WITH bounds AS (
        SELECT b.lo, b.hi, coalesce(b.lo < b.hi, false) AS ok
        FROM (SELECT 1) AS one
        LEFT JOIN LATERAL (
            -- greatest() skips NULLs, and neither an unverified history nor a project without
            -- rollup rows may read as trusted. Rollup retention deletes rows oldest first and
            -- apart from trace retention, so traces can outlive their hour's rows: only hours
            -- after the oldest surviving row are certain to still have theirs (the oldest
            -- hour itself may have lost some of its rows to the end of a batch).
            SELECT CASE WHEN p.aggregate_history_unverified_before IS NOT NULL
                         AND kept.oldest IS NOT NULL THEN
                       greatest(CAST(:first AS timestamptz), p.aggregate_history_unverified_before,
                                kept.oldest + interval '1 hour')
                   END AS lo,
                   least(CAST(:last AS timestamptz), g.next_closed_hour) AS hi
            FROM projects AS p
            JOIN aggregate_job_progress AS g ON g.project_id = p.id AND g.kind = 'rollup_recent'
            CROSS JOIN (
                SELECT min(bucket_start) AS oldest FROM trace_rollups_hourly
                WHERE project_id = :project_id
            ) AS kept
            WHERE p.id = :project_id
        ) AS b ON true
    ), untrusted AS MATERIALIZED (
        SELECT h.bucket_hour AS hour FROM aggregate_dirty_hours AS h, bounds
        WHERE bounds.ok AND h.project_id = :project_id
          AND h.bucket_hour >= bounds.lo AND h.bucket_hour < bounds.hi
          AND (h.pending_consumers & 1) <> 0
        UNION
        SELECT h.bucket_hour FROM aggregate_dirty_shards AS h, bounds
        WHERE bounds.ok AND h.project_id = :project_id
          AND h.bucket_hour >= bounds.lo AND h.bucket_hour < bounds.hi
          AND (h.pending_consumers & 1) <> 0
        UNION
        SELECT h.bucket_hour FROM aggregate_incomplete_hours AS h, bounds
        WHERE bounds.ok AND h.project_id = :project_id
          AND h.bucket_hour >= bounds.lo AND h.bucket_hour < bounds.hi
    ), raw_ranges AS (
        SELECT CAST(:start AS timestamptz) AS s, CAST(:end AS timestamptz) AS e
        FROM bounds WHERE NOT ok
        UNION ALL SELECT CAST(:start AS timestamptz), lo FROM bounds WHERE ok
        UNION ALL SELECT hi, CAST(:end AS timestamptz) FROM bounds WHERE ok
        UNION ALL SELECT hour, hour + interval '1 hour' FROM untrusted
    ), counted AS (
        -- LATERAL with OFFSET 0: each range is its own index range scan on (project_id,
        -- started_at). Joined flat, the planner read every trace of the project once and
        -- compared it with each range, which cost more than the histogram it replaces.
        SELECT c.bucket_start, c.traces, c.errors
        FROM raw_ranges AS r
        CROSS JOIN LATERAL (
            SELECT {raw_bucket} AS bucket_start, count(*) AS traces,
                   count(*) FILTER (WHERE t.error_count > 0) AS errors
            FROM traces AS t
            WHERE t.project_id = :project_id AND t.started_at >= r.s AND t.started_at < r.e
              AND (:environment IS NULL OR t.environment = :environment)
            GROUP BY 1
            OFFSET 0
        ) AS c
        UNION ALL
        SELECT {rollup_bucket}, sum(r.traces), sum(r.errored_traces)
        FROM trace_rollups_hourly AS r, bounds
        WHERE bounds.ok AND r.project_id = :project_id
          AND r.bucket_start >= bounds.lo AND r.bucket_start < bounds.hi
          AND (:environment IS NULL OR r.environment = :environment)
          AND NOT EXISTS (SELECT 1 FROM untrusted AS u WHERE u.hour = r.bucket_start)
        GROUP BY 1
    )
    SELECT bucket_start, CAST(sum(traces) AS bigint) AS traces,
           CAST(sum(errors) AS bigint) AS errors
    FROM counted GROUP BY bucket_start HAVING sum(traces) > 0 ORDER BY bucket_start
"""


def _rolled(unit: str) -> TextClause:
    bucket = f"date_bin(interval '1 {unit}', {{column}}, timestamptz '2000-01-01 00:00:00+00')"
    return text(
        _ROLLED.format(
            raw_bucket=bucket.format(column="t.started_at"),
            rollup_bucket=bucket.format(column="r.bucket_start"),
        )
    ).bindparams(bindparam("environment", type_=String))


_ROLLED_BY = {True: _rolled("hour"), False: _rolled("day")}


def day_bucket() -> ColumnElement[datetime]:
    """The day a trace started in, as the statistics of migration 1017 know it."""
    return func.date_bin(_DAY, Trace.started_at, _ORIGIN, type_=DateTime(timezone=True))


async def trace_histogram(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow, filters: TraceListFilters
) -> TraceHistogramOut:
    """Hour buckets for a window of 48 h or less, else days, truncated in UTC. Buckets without a
    trace are left out; `errors` counts traces with at least one failed span."""
    hourly = window.length <= HOURLY_UP_TO
    if filters.environment_only:
        rows = (
            await db.execute(
                _ROLLED_BY[hourly],
                {
                    "project_id": project_id,
                    "start": window.start,
                    "end": window.end,
                    "first": ceil_hour(window.start),
                    "last": floor_hour(window.end),
                    "environment": filters.environment,
                },
            )
        ).all()
        return _histogram(hourly, rows)
    # date_bin is arithmetic on the instant; date_trunc in a time zone took about 1.5 times as long.
    bucket_start = (
        func.date_bin(_HOUR, Trace.started_at, _ORIGIN, type_=DateTime(timezone=True))
        if hourly
        else day_bucket()
    )
    query = select(
        bucket_start.label("bucket_start"),
        func.count().label("traces"),
        func.count().filter(Trace.error_count > 0).label("errors"),
    )
    query = await filters.narrow(db, query, project_id, window)
    query = query.group_by(bucket_start).order_by(bucket_start)
    with filters.too_broad_on_timeout():
        rows = (await db.execute(query)).all()
    return _histogram(hourly, rows)


def _histogram(hourly: bool, rows: Sequence[Any]) -> TraceHistogramOut:
    return TraceHistogramOut(
        bucket="hour" if hourly else "day",
        buckets=[TraceBucketOut(bucket_start=start, traces=n, errors=e) for start, n, e in rows],
    )

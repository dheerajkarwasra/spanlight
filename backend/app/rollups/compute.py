"""Recompute hourly rollups from the raw spans and traces.

The rollup tables are derived data. :func:`compute_rollups` rebuilds every row of a project in an
hour-aligned range: it deletes the existing rows in that range and inserts fresh aggregates
computed from the raw rows. Delete-then-insert (instead of an upsert) is deliberate: a span that
is sent again with another model or environment moves to a different group, and an upsert would
leave the old group's row behind with counts that no longer exist.

The aggregates use the same definitions as the raw metrics queries in ``app.metrics.queries``, so
a rollup and a raw read of the same hours agree:

* ``span_count`` counts every span of the row's kind; ``errors`` counts spans with status
  ``error``. The reader filters ``kind = 'llm'`` to get ``llm_calls`` and the error rate.
* ``unpriced_calls`` counts ``llm`` spans whose ``cost_usd`` is NULL. It is 0 for other kinds.
* Token columns sum the span columns, treating unknown as 0. ``cost_usd`` sums the known costs
  and stays NULL when no span in the row has one, so an unpriced row is never reported as free.
* Usage rollups (``spans.usage_rollup``: a span repeating the usage of a descendant) are left
  out of the token and cost sums and of ``unpriced_calls``, so one call is never billed twice;
  ``span_count`` and ``errors`` still count them, as a rollup is a span of its kind.
* ``environment`` is the environment of the span's trace. Spans are bucketed by their
  ``started_at`` and traces by theirs.
* ``errored_traces`` counts traces whose ``error_count`` is above 0, which is the rule the trace
  list uses for ``status=error``.

The third table, ``error_rollups_hourly``, is rebuilt in the same transaction and range; its
statements and definitions are in ``app.rollups.error_compute``.
"""

import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Double, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import bind_project
from app.rollups.buckets import BOUNDS_MS, BUCKET_COUNT
from app.rollups.error_compute import DELETE_ERROR_ROLLUPS, INSERT_ERROR_ROLLUPS

HOUR = timedelta(hours=1)
DAY = timedelta(days=1)

_HOUR_BUCKET = "date_trunc('hour', {column}, 'UTC')"


def _histogram(index_column: str) -> str:
    """A 32-element ``integer[]`` counting the rows whose bucket index is each position."""
    counts = ", ".join(
        f"count(*) FILTER (WHERE {index_column} = {position})" for position in range(BUCKET_COUNT)
    )
    return f"CAST(ARRAY[{counts}] AS integer[])"


# The bucket index is the number of bounds strictly below the value, capped at the last bucket,
# which is what `app.rollups.buckets.bucket_index` computes. Postgres' `width_bucket(x, thresholds)`
# counts the thresholds at or below x, so it is applied to the negated value and to the negated
# bounds in reverse order (which are sorted ascending, as it needs): the thresholds at or below
# -value are the bounds at or above value, and `BUCKET_COUNT` minus that count is the number of
# bounds strictly below it. A value that sits exactly on a bound stays in that bound's bucket, 0
# lands in bucket 0 and anything above the last bound is clamped to the last bucket.
#
# This replaces a correlated `SELECT count(*) FROM unnest(bounds) WHERE bound < value`. Postgres
# inlines the CTE that holds the index, so that subquery ran once per row for each of the 64
# `FILTER` clauses that read the index, and a 48 hour recompute on a large project outlived the
# worker's task timeout. `width_bucket` is a binary search with no subquery.
#
# The bounds are bound as a double precision array taken from BOUNDS_MS, so SQL and Python cannot
# drift apart; casting them to numeric or real would change which side of a bound a value falls on.
# Negating a float is exact, so the negated bounds are the same numbers. NaN would land in bucket
# 0 here (Postgres sorts it above every number), but it cannot reach this query: ingest validates
# durations as non-negative and `duration_ms` is derived from two timestamps.
#
# Both are public because alert metrics bucket raw spans the same way (`app.alerts.metrics_queries`)
# and must agree with the stored histograms. A statement that uses `BUCKET_INDEX_SQL` binds
# `neg_bounds` to `list(NEGATED_BOUNDS)`. `least` ignores NULL arguments, so the index of a NULL
# value is the last bucket: callers exclude NULLs themselves.
NEGATED_BOUNDS = tuple(-bound for bound in reversed(BOUNDS_MS))
BUCKET_INDEX_SQL = (
    "least(" + str(BUCKET_COUNT - 1) + ", " + str(BUCKET_COUNT) + " - "
    "width_bucket(-({value}), CAST(:neg_bounds AS double precision[])))"
)

# Two runs for one project (the job and the CLI, or two workers) would both delete the range and
# then both insert, and the second insert would hit the unique constraint. A transaction-level
# advisory lock keyed on the project makes the second run wait for the first to commit, then
# rebuild the same range from the committed data.
_LOCK_PROJECT = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")

# A shared lock on the project row, taken before any rollup row is touched. The INSERTs below need
# this lock anyway (the foreign key check takes it at the end of each statement), but by then the
# DELETEs hold row locks on rollup rows that a project or organization deletion cascades into.
# The deletion holds the project row exclusively and waits for those rows, this job holds the rows
# and waits for the project: a deadlock in which the deletion request can be the one that fails.
# Taking the project first follows the lock order in `app.services.deletion`: a running deletion
# makes this wait and then find no row, and a running rollup makes the deletion wait for it.
_KEY_SHARE_PROJECT = text("SELECT id FROM projects WHERE id = :project_id FOR KEY SHARE")

# The newest span in a range, used by a backfill to jump over days with no spans at all.
_NEWEST_SPAN_BEFORE = text(
    """
    SELECT max(started_at) FROM spans
    WHERE project_id = :project_id AND started_at >= :start AND started_at < :end
    """
)

_DELETE_SPAN_ROLLUPS = text(
    """
    DELETE FROM span_rollups_hourly
    WHERE project_id = :project_id AND bucket_start >= :start AND bucket_start < :end
    """
)

_DELETE_TRACE_ROLLUPS = text(
    """
    DELETE FROM trace_rollups_hourly
    WHERE project_id = :project_id AND bucket_start >= :start AND bucket_start < :end
    """
)

_INSERT_SPAN_ROLLUPS = text(
    f"""
    INSERT INTO span_rollups_hourly (
        project_id, bucket_start, environment, provider, model, kind,
        span_count, errors, input_tokens, output_tokens, cached_tokens, cost_usd,
        unpriced_calls, latency_buckets, ttft_buckets
    )
    WITH scoped AS (
        SELECT s.project_id,
               {_HOUR_BUCKET.format(column="s.started_at")}                    AS bucket_start,
               t.environment,
               s.provider,
               s.model,
               s.kind,
               s.status,
               s.input_tokens,
               s.output_tokens,
               s.cached_tokens,
               s.cost_usd,
               s.usage_rollup,
               {BUCKET_INDEX_SQL.format(value="s.duration_ms")}                   AS latency_index,
               CASE WHEN s.time_to_first_token_ms IS NOT NULL
                    THEN {BUCKET_INDEX_SQL.format(value="s.time_to_first_token_ms")}
               END                                                             AS ttft_index
        FROM spans AS s
        JOIN traces AS t ON t.project_id = s.project_id AND t.trace_id = s.trace_id
        WHERE s.project_id = :project_id
          AND s.started_at >= :start
          AND s.started_at < :end
    )
    SELECT project_id,
           bucket_start,
           environment,
           provider,
           model,
           kind,
           count(*)                                                    AS span_count,
           count(*) FILTER (WHERE status = 'error')                    AS errors,
           coalesce(sum(input_tokens) FILTER (WHERE NOT usage_rollup), 0)  AS input_tokens,
           coalesce(sum(output_tokens) FILTER (WHERE NOT usage_rollup), 0) AS output_tokens,
           coalesce(sum(cached_tokens) FILTER (WHERE NOT usage_rollup), 0) AS cached_tokens,
           sum(cost_usd) FILTER (WHERE NOT usage_rollup)                   AS cost_usd,
           count(*) FILTER (WHERE kind = 'llm' AND cost_usd IS NULL AND NOT usage_rollup)
                                                                       AS unpriced_calls,
           {_histogram("latency_index")}                               AS latency_buckets,
           {_histogram("ttft_index")}                                  AS ttft_buckets
    FROM scoped
    GROUP BY project_id, bucket_start, environment, provider, model, kind
    """
).bindparams(bindparam("neg_bounds", type_=ARRAY(Double())))

_INSERT_TRACE_ROLLUPS = text(
    f"""
    INSERT INTO trace_rollups_hourly (
        project_id, bucket_start, environment, traces, errored_traces
    )
    SELECT t.project_id,
           {_HOUR_BUCKET.format(column="t.started_at")}  AS bucket_start,
           t.environment,
           count(*)                                     AS traces,
           count(*) FILTER (WHERE t.error_count > 0)    AS errored_traces
    FROM traces AS t
    WHERE t.project_id = :project_id
      AND t.started_at >= :start
      AND t.started_at < :end
    GROUP BY t.project_id, 2, t.environment
    """
)


def floor_hour(moment: datetime) -> datetime:
    """The start of the UTC hour that contains ``moment``."""
    return moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def ceil_hour(moment: datetime) -> datetime:
    """``moment`` rounded up to a whole UTC hour (unchanged when already on one)."""
    floored = floor_hour(moment)
    return floored if floored == moment.astimezone(UTC) else floored + HOUR


async def compute_rollups(
    db: AsyncSession, project_id: uuid.UUID, start: datetime, end: datetime
) -> int:
    """Rebuild a project's rollup rows for the hours in ``[start, end)``.

    ``start`` is rounded down and ``end`` up to a whole UTC hour, so every hour that overlaps the
    range is recomputed in full. The rows in that range are deleted and the fresh aggregates are
    inserted in the caller's transaction, which this function does not commit: a reader sees
    either the old rows or the new ones, never a half-written hour.

    The transaction is bound to ``project_id`` (the same ``SET LOCAL`` the API uses), so the
    row-level security policies confine every statement to that project. The caller does not need
    to bypass RLS to run this.

    Returns the number of rows written to the three tables. A range of one empty hour returns 0,
    and so does a project that has been deleted (nothing is written for it).
    """
    aligned_start = floor_hour(start)
    aligned_end = ceil_hour(end)
    if aligned_end <= aligned_start:
        return 0
    await bind_project(db, project_id)
    project = await db.execute(_KEY_SHARE_PROJECT, {"project_id": project_id})
    if project.scalar_one_or_none() is None:
        return 0
    await db.execute(_LOCK_PROJECT, {"key": f"span-rollups:{project_id}"})
    params: dict[str, Any] = {"project_id": project_id, "start": aligned_start, "end": aligned_end}
    await db.execute(_DELETE_SPAN_ROLLUPS, params)
    await db.execute(_DELETE_TRACE_ROLLUPS, params)
    await db.execute(DELETE_ERROR_ROLLUPS, params)
    results = [
        await db.execute(_INSERT_SPAN_ROLLUPS, {**params, "neg_bounds": list(NEGATED_BOUNDS)}),
        await db.execute(_INSERT_TRACE_ROLLUPS, params),
        await db.execute(INSERT_ERROR_ROLLUPS, params),
    ]
    return sum(int(getattr(result, "rowcount", 0)) for result in results)


async def backfill_rollups(
    db: AsyncSession,
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
    *,
    before_chunk: Callable[[], Awaitable[None]] | None = None,
    deadline: float | None = None,
) -> int:
    """Recompute ``[start, end)`` in chunks of at most one day, newest first.

    Each chunk is committed on its own, so a long backfill holds no long transaction and an
    interruption keeps the chunks already written. The newest days go first because they are the
    ones the dashboards read most. A chunk always ends at the newest span left in the range, so
    days without spans are skipped instead of producing empty chunks: every chunk writes at least
    one row, which is what lets the job tell how far a backfill got.

    ``before_chunk`` runs before each chunk (the worker uses it to extend its lease). ``deadline``
    is a :func:`time.monotonic` value after which no new chunk is started. Returns the total
    number of rows written.
    """
    aligned_start = floor_hour(start)
    chunk_end = ceil_hour(end)
    total = 0
    while chunk_end > aligned_start:
        if deadline is not None and time.monotonic() >= deadline:
            break
        if before_chunk is not None:
            await before_chunk()
        await bind_project(db, project_id)
        newest = (
            await db.execute(
                _NEWEST_SPAN_BEFORE,
                {"project_id": project_id, "start": aligned_start, "end": chunk_end},
            )
        ).scalar_one()
        if newest is None:
            await db.rollback()
            break
        chunk_end = min(chunk_end, ceil_hour(newest + timedelta(microseconds=1)))
        chunk_start = max(aligned_start, chunk_end - DAY)
        total += await compute_rollups(db, project_id, chunk_start, chunk_end)
        await db.commit()
        chunk_end = chunk_start
    return total

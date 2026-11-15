"""Bounded per-day trace probes for live user totals and pending aggregate work."""

# Materialize UTC bounds so PostgreSQL 17.12 can use them as index conditions under RLS.
TAIL_FILTER = """
          AND t.started_at >= :start
          AND t.started_at > now() - CAST(:tail_max AS interval)
          AND t.started_at < :end
          AND t.started_at >= (
              WITH days AS MATERIALIZED (
                  SELECT d.day::date AS day, d.day AT TIME ZONE 'UTC' AS start_at,
                         (d.day + interval '1 day') AT TIME ZONE 'UTC' AS end_at
                  FROM generate_series(
                      CAST(greatest(CAST(:first_day AS date),
                                    ((now() - CAST(:tail_max AS interval))
                                         AT TIME ZONE 'UTC')::date)
                           AS timestamp),
                      CAST(CAST(:end_day AS date) - 1 AS timestamp), interval '1 day') AS d(day))
              SELECT coalesce(min(f.first_at), 'infinity')
              FROM days AS d
              LEFT JOIN refreshed AS r ON r.day = d.day
              CROSS JOIN LATERAL (
                  SELECT min(n.started_at) AS first_at FROM traces AS n
                  WHERE n.project_id = :project_id
                    AND n.started_at > greatest(r.computed_at, now() - CAST(:tail_max AS interval))
                    AND n.started_at >= d.start_at
                    AND n.started_at < d.end_at) AS f)
          AND t.started_at > coalesce(
              (SELECT r.computed_at FROM refreshed AS r
               WHERE r.day = (t.started_at AT TIME ZONE 'UTC')::date), '-infinity')
          AND (SELECT bool_and(complete) FROM aggregate_coverage)"""


def pending_sql(refreshed: str, trace_filter: str) -> str:
    """The window's latest recompute (`computed_at`) and how many of its traces are not counted
    yet (`pending_traces`, at most `:cap`): those started after the latest recompute of their own
    UTC day, and every trace of a day without rows.

    `refreshed` selects `day, computed_at` (the latest recompute per day) of the derived table;
    `trace_filter` narrows `traces AS t` past the project. Binds `:project_id`, `:first_day`,
    `:end_day`, `:start` (midnight of the first day) and `:cap`. Per day, because older days are
    recomputed less often than the last three; by trace start, the time the refreshes assign
    traces to days by. The lateral probe gives each day one `traces_project_started_idx` range,
    `(computed_at, next midnight)`, empty for a day recomputed after it ended (a plain join let
    the planner drop the day bounds and read the project from `-infinity`). The inner `LIMIT`
    keeps the planner from flattening the probe into such a join; the outer one stops the nested
    loop after `:cap` index entries in total, whatever the number of days.
    """
    return f"""
    WITH refreshed AS ({refreshed}),
    days AS (
        SELECT CAST(:first_day AS date) + i AS day,
               CAST(:start AS timestamptz) + i * interval '24 hours' AS start_at
        FROM generate_series(0, CAST(:end_day AS date) - CAST(:first_day AS date) - 1) AS i
    )
    SELECT (SELECT max(computed_at) FROM refreshed) AS computed_at,
           (SELECT count(*) FROM (
               SELECT 1
               FROM days
               LEFT JOIN refreshed ON refreshed.day = days.day
               CROSS JOIN LATERAL (
                   SELECT 1 FROM traces AS t
                   WHERE t.project_id = :project_id{trace_filter}
                     AND t.started_at > greatest(
                         refreshed.computed_at, days.start_at - interval '1 microsecond'
                     )
                     AND t.started_at < days.start_at + interval '24 hours'
                   LIMIT :cap
               ) AS hit
               LIMIT :cap
           ) AS capped) AS pending_traces
"""

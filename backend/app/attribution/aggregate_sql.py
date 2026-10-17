"""The `grouped` CTE: the traces of a window, summed per group.

One definition for the two callers, the daily attribution rollup and the raw read of a window of
24 hours or less, so the two agree by construction (spec §5.3).

- A trace belongs to the UTC day of its `started_at` and to the group of its own dimension:
  `metadata ->> :key`, `name`, `external_user_id` or `release`. A NULL dimension is the `none`
  bucket (`group_value` NULL), a real group.
- Its figures come from its spans whatever day they started. `llm_calls`, `errors` (status
  `error`) and `unpriced_calls` (`cost_usd IS NULL`) count its `llm` spans. `cost_usd` and the
  token totals sum every span, priced first per trace with the exact expression that stores
  `traces.cost_usd` (`app.ingest.store._RECOMPUTE_ROLLUPS`), then summed per group. So a group's
  cost is the sum of its traces' `cost_usd`, NULL as absent, and the rows of a window add up to
  the window's trace totals (the ledger property, spec §5.6).
- `retries` sums Bet 2's per-trace `retry_calls`; a trace with NULL there (not recomputed since
  migration 0700) adds nothing, and a group of such traces only reads NULL, never 0 (spec §13).
  `retry_cost_usd` follows `retry_cost_sum`: such a trace adds nothing either, but a trace with
  known retries and a NULL retry cost (a retry it could not price) makes the group's retry cost
  unknown, as Bet 2 makes the trace's.
- Money sums are NULL from $1,000,000 up, per trace (as `traces.cost_usd` is) and again per
  group: they would not fit `numeric(14, 8)`, the type of every stored cost, and an overflow would
  fail the refresh rather than say "unknown". So a group of $1M or more reads NULL, and the
  window's total over the rows falls short of the traces' by that group: the ledger holds below
  that edge.

`grouped_cte` returns SQL text with named binds, for the caller to prefix with `WITH` and follow
with its own CTEs or SELECT; this module does no I/O. Binds: `:project_id`, `:start`, `:end`
(the window, by trace `started_at`), `:key` for the `metadata` kind and `:environment` when
`with_environment`. The key is always bound, never interpolated, so any key is safe.
"""

from typing import Literal

GroupKind = Literal["metadata", "trace_name", "user", "release"]

GROUP_EXPRESSIONS: dict[GroupKind, str] = {
    "metadata": "t.metadata ->> CAST(:key AS text)",
    "trace_name": "t.name",
    "user": "t.external_user_id",
    "release": "t.release",
}


def money_sum(expression: str) -> str:
    """`sum(expression)`, NULL (unknown) from $1,000,000 up, where `numeric(14, 8)` overflows."""
    return f"CASE WHEN sum({expression}) < 1000000 THEN sum({expression}) END"


def retry_cost_sum(retries: str, retry_cost: str) -> str:
    """The retry cost of rows summed together, each with a retry count and a retry cost.

    A row with both NULL (a trace not recomputed since 0700, or a rollup row made only of such
    traces) adds nothing. A row with a known count and a NULL cost (an unpriced retry, or a
    group that reached $1M) makes the sum unknown. The same rule sums per-trace rows here and
    rollup rows across days: `retry_cost_sum("r.retries", "r.retry_cost_usd")`.
    """
    unknown = f"bool_or({retries} IS NOT NULL AND {retry_cost} IS NULL)"
    return f"CASE WHEN {unknown} THEN NULL ELSE {money_sum(retry_cost)} END"


# The span filter of the cost and token sums, which counts never take: a usage rollup repeats a
# descendant's usage (migration 0900), so it is counted as a call and never summed.
_SUMMED = "FILTER (WHERE s.usage_rollup = false)"

# One row per trace. With no span joined, `s.kind` is NULL, so the counts' filters reject the
# row and `count(*)` reads 0 (and needs no column the spans' covering index lacks). The span
# side is bounded below by the window start, which drops nothing (a trace starts when its first
# span does) and lets the planner use the spans' time index; it has no upper bound, so a trace's
# late spans count on the day the trace started.
_PER_TRACE = """
        SELECT {day}t.environment,
               {group_expression} AS group_value,
               t.retry_calls,
               t.retry_cost_usd,
               count(*) FILTER (WHERE s.kind = 'llm') AS llm_calls,
               count(*) FILTER (WHERE s.kind = 'llm' AND s.status = 'error') AS errors,
               count(*) FILTER (WHERE s.kind = 'llm' AND s.cost_usd IS NULL)
                   AS unpriced_calls,
               coalesce(sum(s.input_tokens) {summed}, 0) AS input_tokens,
               coalesce(sum(s.output_tokens) {summed}, 0) AS output_tokens,
               CASE WHEN sum(s.cost_usd) {summed} < 1000000
                    THEN sum(s.cost_usd) {summed} END AS cost_usd
        FROM traces AS t
        LEFT JOIN spans AS s
               ON s.project_id = t.project_id
              AND s.trace_id = t.trace_id
              AND s.started_at >= :start
        WHERE t.project_id = :project_id
          AND t.started_at >= :start
          AND t.started_at < :end{environment}
        GROUP BY t.project_id, t.trace_id"""

_GROUPED = """grouped AS (
    SELECT {day}per_trace.environment,
           per_trace.group_value,
           count(*) AS traces,
           sum(per_trace.llm_calls)::bigint AS llm_calls,
           sum(per_trace.errors)::bigint AS errors,
           sum(per_trace.unpriced_calls)::bigint AS unpriced_calls,
           sum(per_trace.input_tokens)::bigint AS input_tokens,
           sum(per_trace.output_tokens)::bigint AS output_tokens,
           {cost} AS cost_usd,
           sum(per_trace.retry_calls)::bigint AS retries,
           {retry_cost} AS retry_cost_usd
    FROM ({per_trace}
    ) AS per_trace
    GROUP BY {day}per_trace.environment, per_trace.group_value
)"""


def grouped_cte(kind: GroupKind, *, by_day: bool, with_environment: bool) -> str:
    """The SQL of the CTE `grouped` (without `WITH`) for one kind of group.

    Columns: `day` (the UTC date of the traces' start, only when `by_day`), `environment`,
    `group_value` (NULL for the `none` bucket), `traces`, `llm_calls`, `errors`,
    `unpriced_calls`, `input_tokens`, `output_tokens`, `cost_usd`, `retries`, `retry_cost_usd`.
    One row per (day, environment, group_value); `with_environment` keeps the traces of
    `:environment` only.
    """
    per_trace = _PER_TRACE.format(
        day="(t.started_at AT TIME ZONE 'UTC')::date AS day,\n               " if by_day else "",
        group_expression=GROUP_EXPRESSIONS[kind],
        summed=_SUMMED,
        environment="\n          AND t.environment = :environment" if with_environment else "",
    )
    return _GROUPED.format(
        day="per_trace.day, " if by_day else "",
        per_trace=per_trace,
        cost=money_sum("per_trace.cost_usd"),
        retry_cost=retry_cost_sum("per_trace.retry_calls", "per_trace.retry_cost_usd"),
    )

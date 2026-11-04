"""Raw-span reads behind the metrics endpoints: the parts of a window not read from the rollups.

A window is planned into whole rollup hours and at most two raw ranges (``app.metrics.service``),
and every statement here reads a list of groups of such ranges in one round trip: the ranges are
passed as three arrays (start, end, group) and joined to ``spans`` with ``unnest``, so each one is
an index range scan, and the result is grouped by the group number (the overview reads its current
and previous window as groups 0 and 1). A group with no span has no row. The ranges of one group
must not overlap.

Every statement has two forms, picked by whether an environment filter was given:

* Without a filter it reads ``spans`` alone. Every span has its trace (the composite foreign key
  guarantees it), so joining ``traces`` would neither add nor drop a row; it would only make the
  database read the whole of ``traces`` for the project, which at tens of millions of spans is
  the slowest part of the request.
* With a filter it reads ``traces`` too, because the environment belongs to the trace: once per
  trace of the ranges, not once per span (see ``_IN_ENVIRONMENT``).

Both forms return the same rows for the same data. The filter-free form reads only the columns
that ``spans_project_started_window_idx`` carries, so it is answered from the index without
touching the table (see migrations 0016 and 1004). Keep the two in step: a new column read here
belongs in the index. ``usage_rollup`` (migration 0900), read to leave usage rollups out of the
token and cost sums (the counts keep them), was added by 1004 after the load test showed every
row of the window fetched from the table without it.

Latency comes in one of two forms. Exact percentiles (``percentile_cont``) when the window reads
no rollup hour. Otherwise the llm latency histogram, bucketed exactly as the rollup job does
(``app.rollups.compute``), so the raw part and the rollup hours add up element-wise. It is only
asked for when it is needed, and read in two levels (see ``_by_bucket``).

The queries run in the caller's session and rely on its project binding for row-level security;
the explicit ``project_id`` filter is a second guard, not the only one.
"""

import uuid
from collections.abc import Sequence
from functools import cache
from typing import Any, Literal

from sqlalchemy import DateTime, Double, Integer, Row, String, TextClause, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.window import TimeWindow
from app.rollups.buckets import BUCKET_COUNT
from app.rollups.compute import BUCKET_INDEX_SQL, NEGATED_BOUNDS, floor_hour
from app.rollups.coverage_reads import CoverageState, read_covered_groups

Groups = Sequence[Sequence[TimeWindow]]
"""Groups of disjoint ranges; the statements return one row per non-empty group (and bucket)."""

_RANGES = """
    FROM unnest(
             CAST(:starts AS timestamptz[]),
             CAST(:ends AS timestamptz[]),
             CAST(:groups AS integer[])
         ) AS w(start_at, end_at, grp)
    JOIN spans AS s
      ON s.project_id = :project_id
     AND s.started_at >= w.start_at
     AND s.started_at < w.end_at
"""

# Joining `traces` to the spans would probe its key once per span (most of the read's time); this
# probes it once per trace instead: the ranges' distinct trace ids come from a second read of the
# same index range, each is looked up (`LIMIT 1` keeps that a key probe, whatever the planner
# estimates), and the spans of the traces in the environment are kept. The rows are the join's:
# `(project_id, trace_id)` is the key of `traces`.
_IN_ENVIRONMENT = f"""    WHERE s.trace_id IN (
        SELECT d.trace_id
        FROM (SELECT DISTINCT s.trace_id{_RANGES}) AS d
        CROSS JOIN LATERAL (
            SELECT t.environment FROM traces AS t
            WHERE t.project_id = :project_id AND t.trace_id = d.trace_id
            LIMIT 1
        ) AS t
        WHERE t.environment = :environment)
"""

_SPANS = {False: _RANGES + "    WHERE TRUE\n", True: _RANGES + _IN_ENVIRONMENT}

_SUMS = """
           sum(s.cost_usd) FILTER (WHERE NOT s.usage_rollup)                   AS cost_usd,
           coalesce(sum(s.input_tokens) FILTER (WHERE NOT s.usage_rollup), 0)  AS input_tokens,
           coalesce(sum(s.output_tokens) FILTER (WHERE NOT s.usage_rollup), 0) AS output_tokens"""

_LLM_COUNTS = """
           count(*) FILTER (WHERE s.kind = 'llm')                              AS llm_calls,
           count(*) FILTER (WHERE s.kind = 'llm' AND s.status = 'error')       AS llm_errors,
           count(*) FILTER (WHERE s.kind = 'llm' AND s.cost_usd IS NULL AND NOT s.usage_rollup)
                                                                               AS unpriced_calls,"""

# The histogram forms aggregate in two levels: the inner query groups the spans of each group by
# their bucket index, computed once per span, and the outer one adds those few rows up and builds
# the array (32 filtered counts per span cost more than the rest of the read). Only an llm span
# with a duration has a bucket, as in `app.alerts.metrics_queries._by_bucket`; any other has
# -1, so NULL is free to mark the row of a grouping set without the index.
_BUCKET = (
    "CASE WHEN s.kind = 'llm' AND s.duration_ms IS NOT NULL"
    f" THEN {BUCKET_INDEX_SQL.format(value='s.duration_ms')} ELSE -1 END AS b"
)
_BUCKET_COUNTS = (
    "CAST(ARRAY["
    + ", ".join(f"coalesce(sum(n) FILTER (WHERE b = {k}), 0)" for k in range(BUCKET_COUNT))
    + "] AS integer[]) AS latency_buckets"
)
_TOTALS: tuple[str, ...] = (
    "llm_calls",
    "llm_errors",
    "unpriced_calls",
    "cost_usd",
    "input_tokens",
    "output_tokens",
)


def _positions(names: str) -> str:
    """``GROUP BY`` positions of the key columns, which come first."""
    return ", ".join(str(i) for i in range(1, names.count(",") + 2))


def _by_bucket(
    keys: str,
    names: str,
    aggregates: str,
    totals: Sequence[str],
    spans: str,
    exact: str = "",
    exact_names: Sequence[str] = (),
) -> str:
    """The two-level histogram form: ``keys`` are the group columns, named ``names``, and
    ``totals`` the names of ``aggregates``.

    The outer level adds the bucket rows up, cast back to the single-level types (bigint, numeric
    for cost). An exact percentile cannot be added up: with ``exact`` (the percentiles, named
    ``exact_names``) the inner level also groups without the index, and that row (``b IS NULL``)
    gives the percentiles and the totals.
    """
    keys_at = _positions(names)
    group = f"{keys_at}, {names.count(',') + 2}"
    if exact:
        columns = [f"max({c}) FILTER (WHERE b IS NULL) AS {c}" for c in (*totals, *exact_names)]
        group, aggregates = f"GROUPING SETS (({group}), ({keys_at}))", f"{aggregates}, {exact}"
    else:
        columns = [
            f"sum({c}) AS {c}" if c == "cost_usd" else f"CAST(sum({c}) AS bigint) AS {c}"
            for c in totals
        ]
    return (
        f"SELECT {names}, {', '.join(columns)},\n           {_BUCKET_COUNTS}\n"
        f"    FROM (SELECT {keys}, {_BUCKET}, count(*) AS n,{aggregates}\n"
        f"{spans}    GROUP BY {group}) AS h\n    GROUP BY {names}"
    )


def _percentile(q: float, name: str, llm_only: bool = True) -> str:
    only = " FILTER (WHERE s.kind = 'llm')" if llm_only else ""
    return f"percentile_cont({q}) WITHIN GROUP (ORDER BY s.duration_ms){only} AS {name}"


Statement = Literal["kpis", "timeseries", "models"]


@cache
def _statement(kind: Statement, environment: bool, histogram: bool | Literal["both"]) -> TextClause:
    """One statement for a filter form and a latency form.

    The KPI and model reads take exact p50/p95 or the histogram, or both (``"both"``: the KPIs of
    groups that differ in form). The time series takes the exact p95 always (a bucket of raw spans
    only stays exact) and the histogram as well when asked.
    """
    spans, totals, aggregates = _SPANS[environment], _TOTALS, f"{_LLM_COUNTS}{_SUMS}"
    if kind == "kpis":
        keys, names = "w.grp", "grp"
        latency = f"{_percentile(0.5, 'p50_ms')}, {_percentile(0.95, 'p95_ms')}"
    elif kind == "timeseries":
        keys = "w.grp, date_trunc(:bucket, s.started_at, 'UTC') AS bucket_start"
        names, latency = "grp, bucket_start", _percentile(0.95, "p95_ms")
    else:
        keys, names = "w.grp, s.provider, s.model", "grp, provider, model"
        totals = ("calls", "errors", "cost_usd", "input_tokens", "output_tokens")
        aggregates = (
            f" count(*) AS calls, count(*) FILTER (WHERE s.status = 'error') AS errors,{_SUMS}"
        )
        latency = f"{_percentile(0.5, 'p50_ms', False)}, {_percentile(0.95, 'p95_ms', False)}"
        spans += "      AND s.kind = 'llm'\n"
    if not histogram:
        sql = (
            f"SELECT {keys},{aggregates},\n           {latency}\n"
            f"{spans}    GROUP BY {_positions(names)}"
        )
    elif kind == "timeseries":
        sql = _by_bucket(keys, names, aggregates, totals, spans, latency, ("p95_ms",))
    elif histogram == "both":
        exact = ("p50_ms", "p95_ms")
        sql = _by_bucket(keys, names, aggregates, totals, spans, latency, exact)
    else:
        sql = _by_bucket(keys, names, aggregates, totals, spans)
    statement = text(sql).bindparams(
        bindparam("starts", type_=ARRAY(DateTime(timezone=True))),
        bindparam("ends", type_=ARRAY(DateTime(timezone=True))),
        bindparam("groups", type_=ARRAY(Integer())),
    )
    if environment:
        statement = statement.bindparams(bindparam("environment", type_=String))
    if histogram:
        statement = statement.bindparams(bindparam("neg_bounds", type_=ARRAY(Double())))
    return statement


_TRACE_COUNTS = {
    environment: text(
        f"""
        SELECT w.grp, count(*) AS traces
        FROM unnest(
                 CAST(:starts AS timestamptz[]),
                 CAST(:ends AS timestamptz[]),
                 CAST(:groups AS integer[])
             ) AS w(start_at, end_at, grp)
        JOIN traces AS t
          ON t.project_id = :project_id
         AND t.started_at >= w.start_at
         AND t.started_at < w.end_at
        {"WHERE t.environment = :environment" if environment else ""}
        GROUP BY w.grp
        """
    ).bindparams(
        bindparam("starts", type_=ARRAY(DateTime(timezone=True))),
        bindparam("ends", type_=ARRAY(DateTime(timezone=True))),
        bindparam("groups", type_=ARRAY(Integer())),
        *([bindparam("environment", type_=String)] if environment else []),
    )
    for environment in (False, True)
}


def _params(
    project_id: uuid.UUID, groups: Groups, environment: str | None, histogram: bool = False
) -> dict[str, Any] | None:
    """The bound values, or ``None`` when no group has a non-empty range (nothing to read)."""
    ranges = [(g, r) for g, group in enumerate(groups) for r in group if r.start < r.end]
    if not ranges:
        return None
    params: dict[str, Any] = {
        "project_id": project_id,
        "starts": [r.start for _, r in ranges],
        "ends": [r.end for _, r in ranges],
        "groups": [g for g, _ in ranges],
    }
    if environment is not None:
        # Only the filtered form has the parameter.
        params["environment"] = environment
    if histogram:
        params["neg_bounds"] = list(NEGATED_BOUNDS)
    return params


async def read_span_kpis(
    db: AsyncSession,
    project_id: uuid.UUID,
    groups: Groups,
    environment: str | None,
    histogram: Sequence[bool] | bool = False,
    coverage: Sequence[CoverageState] | None = None,
) -> list[Row[Any] | None]:
    """One row of span aggregates per group, ``None`` for a group without spans (see
    ``app.metrics.service`` for the meaning). ``histogram`` and ``coverage`` may be given per
    group; a row has the latency form of its group."""
    rows: list[Row[Any] | None] = [None] * len(groups)
    if isinstance(histogram, bool):
        histogram = [histogram] * len(groups)
    forms = {h for h, group in zip(histogram, groups, strict=True) if _reads(group)}
    form: bool | Literal["both"] = forms.pop() if len(forms) == 1 else "both"
    params = _params(project_id, groups, environment, form is not False)
    if params is not None:
        statement = _statement("kpis", environment is not None, form)
        for row in await _covered(db, statement, params, project_id, groups, coverage):
            rows[int(row.grp)] = row
    return rows


async def read_trace_counts(
    db: AsyncSession,
    project_id: uuid.UUID,
    groups: Groups,
    environment: str | None,
    coverage: Sequence[CoverageState] | None = None,
) -> list[int]:
    """The number of traces that started inside each group's ranges."""
    counts = [0] * len(groups)
    params = _params(project_id, groups, environment)
    if params is not None:
        for row in await _covered(
            db, _TRACE_COUNTS[environment is not None], params, project_id, groups, coverage
        ):
            counts[int(row.grp)] = int(row.traces)
    return counts


async def read_timeseries(
    db: AsyncSession,
    project_id: uuid.UUID,
    ranges: Sequence[TimeWindow],
    environment: str | None,
    bucket: Literal["hour", "day"],
    histogram: bool = False,
    coverage: CoverageState | None = None,
) -> list[Row[Any]]:
    """One row per UTC bucket of the ranges that holds a span; the caller fills the gaps."""
    params = _params(project_id, [ranges], environment, histogram)
    if params is None:
        return []
    statement = _statement("timeseries", environment is not None, histogram)
    states = None if coverage is None else [coverage]
    return await _covered(db, statement, {**params, "bucket": bucket}, project_id, [ranges], states)


async def read_models(
    db: AsyncSession,
    project_id: uuid.UUID,
    ranges: Sequence[TimeWindow],
    environment: str | None,
    histogram: bool = False,
    coverage: CoverageState | None = None,
) -> list[Row[Any]]:
    """Every provider and model pair with llm calls in the ranges; the caller sorts and cuts."""
    params = _params(project_id, [ranges], environment, histogram)
    if params is None:
        return []
    statement = _statement("models", environment is not None, histogram)
    states = None if coverage is None else [coverage]
    return await _covered(db, statement, params, project_id, [ranges], states)


def _reads(group: Sequence[TimeWindow]) -> bool:
    return any(r.start < r.end for r in group)


async def _covered(
    db: AsyncSession,
    statement: TextClause,
    params: dict[str, Any],
    project_id: uuid.UUID,
    groups: Groups,
    coverage: Sequence[CoverageState] | None,
) -> list[Row[Any]]:
    """Read with each group's coverage judged over its own hours and recorded in its own state;
    a group with nothing to read is not judged (as when it is read alone)."""
    return await read_covered_groups(
        db,
        statement,
        params,
        project_id,
        [
            (
                g,
                floor_hour(min(window.start for window in group)),
                max(window.end for window in group),
                None if coverage is None else coverage[g],
            )
            for g, group in enumerate(groups)
            if _reads(group)
        ],
        relevant_consumers=0,
        raw=True,
        grouped=True,
    )

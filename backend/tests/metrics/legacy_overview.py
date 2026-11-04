"""Test oracle: the overview as it was before Task 17 (two passes, one per period).

Frozen copy of the pre-task `app.metrics.service.overview` / `_window_kpis` / `_rollup_hours`,
`app.metrics.queries.read_span_kpis` / `read_trace_counts` / `_covered` / `_by_bucket` /
`_statement`, `app.rollups.coverage_reads.read_covered` and `app.rollups.queries.read_rollups`.
Only the split is a parameter (fixed by the test) instead of being read per window. Pieces the
task did not change (SQL fragments, `_kpis`, `HourlyRollup`) are imported. Delete this module
once the one-pass overview has shipped.
"""

import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from functools import cache
from typing import Any

from sqlalchemy import DateTime, Double, Integer, Row, String, TextClause, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import BindParameter
from sqlalchemy.sql.visitors import iterate

from app.alerts.metrics import WindowPlan, plan_window
from app.api.schemas import KpisOut, OverviewOut
from app.api.window import TimeWindow
from app.metrics.queries import (
    _BUCKET,
    _BUCKET_COUNTS,
    _LLM_COUNTS,
    _SPANS,
    _SUMS,
    _TOTALS,
    _TRACE_COUNTS,
    Groups,
    _params,
    _percentile,
    _positions,
)
from app.metrics.service import _kpis
from app.rollups.buckets import BUCKET_COUNT
from app.rollups.compute import floor_hour
from app.rollups.coverage_reads import CoverageState
from app.rollups.dirty import ALL, HOURLY
from app.rollups.queries import _SPAN_HOURS, _TRACE_HOURS, HourlyRollup, RollupSeries
from app.rollups.queries import _params as _rollup_params


async def read_covered(
    db: AsyncSession,
    statement: TextClause,
    params: dict[str, Any],
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
    state: CoverageState | None = None,
    *,
    relevant_consumers: int,
    raw: bool = False,
) -> list[Row[Any]]:
    if type(relevant_consumers) is not int or not 0 <= relevant_consumers <= ALL:
        raise ValueError("relevant_consumers must be an integer mask from 0 through ALL")
    gate = "WHERE (SELECT complete FROM aggregate_coverage)" if raw else ""
    wrapped = text(f"""
        WITH recent_progress AS (
            SELECT admitted_hour, next_closed_hour, closed_pending_since
            FROM aggregate_job_progress
            WHERE project_id = :coverage_project AND kind = 'rollup_recent'
        ), scheduled_pending AS (
            SELECT coalesce(closed_pending_since,
                            next_closed_hour + interval '1 hour 10 minutes') AS due_at
            FROM recent_progress
            WHERE (CAST(:relevant_consumers AS integer) & 1) <> 0
              AND greatest(CAST(:coverage_start AS timestamptz), admitted_hour, next_closed_hour)
                  < least(CAST(:coverage_end AS timestamptz),
                          date_trunc('hour', statement_timestamp() - interval '10 minutes', 'UTC'))
        ), aggregate_coverage AS (
            SELECT coalesce((SELECT :coverage_start >= p.aggregate_history_unverified_before
                            FROM projects p WHERE p.id = :coverage_project), false)
                   AND NOT EXISTS (
                       SELECT 1 FROM aggregate_incomplete_hours h
                       WHERE h.project_id = :coverage_project
                         AND h.bucket_hour >= :coverage_start AND h.bucket_hour < :coverage_end
                   ) AS complete,
                   least((SELECT min(h.first_marked_at) FROM (
                        SELECT project_id, bucket_hour, first_marked_at, pending_consumers
                        FROM aggregate_dirty_hours
                        UNION ALL
                        SELECT project_id, bucket_hour, first_marked_at, pending_consumers
                        FROM aggregate_dirty_shards
                    ) h
                    WHERE h.project_id = :coverage_project
                      AND h.bucket_hour >= :coverage_start AND h.bucket_hour < :coverage_end
                      AND (h.pending_consumers & CAST(:relevant_consumers AS integer)) <> 0
                   ), (SELECT min(due_at) FROM scheduled_pending)) AS oldest_pending_at,
                   ((CAST(:relevant_consumers AS integer) & 1) = 0
                     OR EXISTS (SELECT 1 FROM recent_progress)) AS progress_present
        ), aggregate_values AS (
            SELECT source.*, true AS aggregate_present FROM ({statement.text}) source {gate}
        )
        SELECT c.progress_present AS aggregate_progress_present,
               c.complete AS aggregate_complete, c.oldest_pending_at AS aggregate_pending_at, v.*
        FROM aggregate_coverage c LEFT JOIN aggregate_values v ON true
    """).bindparams(*(node for node in iterate(statement) if isinstance(node, BindParameter)))
    rows = (
        await db.execute(
            wrapped,
            {
                **params,
                "coverage_project": project_id,
                "coverage_start": start,
                "coverage_end": end,
                "relevant_consumers": relevant_consumers,
            },
        )
    ).all()
    if not rows[0].aggregate_progress_present:
        raise RuntimeError("Required recent aggregate admission is missing")
    if not rows[0].aggregate_complete and state is not None:
        state.unavailable = True
    pending = rows[0].aggregate_pending_at
    if (
        state is not None
        and pending is not None
        and (state.oldest_pending_at is None or pending < state.oldest_pending_at)
    ):
        state.oldest_pending_at = pending
    return [row for row in rows if row.aggregate_present is True]


async def read_rollups(
    db: AsyncSession,
    project_id: uuid.UUID,
    window: TimeWindow,
    environment: str | None,
    coverage: CoverageState | None = None,
) -> RollupSeries:
    params = _rollup_params(project_id, window, environment)
    bounds = (floor_hour(window.start), window.end)
    span_rows = await read_covered(
        db, _SPAN_HOURS, params, project_id, *bounds, coverage, relevant_consumers=HOURLY
    )
    trace_rows = await read_covered(
        db, _TRACE_HOURS, params, project_id, *bounds, coverage, relevant_consumers=HOURLY
    )
    hours: dict[datetime, HourlyRollup] = {
        row.bucket_start: HourlyRollup(
            bucket_start=row.bucket_start,
            traces=0,
            llm_calls=int(row.llm_calls),
            llm_errors=int(row.llm_errors),
            unpriced_calls=int(row.unpriced_calls),
            cost_usd=row.cost_usd,
            input_tokens=int(row.input_tokens),
            output_tokens=int(row.output_tokens),
            latency_buckets=[int(count) for count in row.latency_buckets],
        )
        for row in span_rows
    }
    for row in trace_rows:
        traces = int(row.traces)
        existing = hours.get(row.bucket_start)
        if existing is None:
            hours[row.bucket_start] = HourlyRollup(
                bucket_start=row.bucket_start,
                traces=traces,
                llm_calls=0,
                llm_errors=0,
                unpriced_calls=0,
                cost_usd=None,
                input_tokens=0,
                output_tokens=0,
                latency_buckets=[0] * BUCKET_COUNT,
            )
        else:
            hours[row.bucket_start] = replace(existing, traces=traces)
    return RollupSeries(hours=[hours[start] for start in sorted(hours)])


def _by_bucket(
    keys: str, names: str, aggregates: str, totals: Sequence[str], spans: str, p95: str = ""
) -> str:
    keys_at = _positions(names)
    group = f"{keys_at}, {names.count(',') + 2}"
    if p95:
        columns = [f"max({c}) FILTER (WHERE b IS NULL) AS {c}" for c in (*totals, "p95_ms")]
        group, aggregates = f"GROUPING SETS (({group}), ({keys_at}))", f"{aggregates}, {p95}"
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


@cache
def _kpi_statement(environment: bool, histogram: bool) -> TextClause:
    """The pre-task `_statement("kpis", ...)`."""
    spans, aggregates = _SPANS[environment], f"{_LLM_COUNTS}{_SUMS}"
    keys, names = "w.grp", "grp"
    latency = f"{_percentile(0.5, 'p50_ms')}, {_percentile(0.95, 'p95_ms')}"
    if not histogram:
        sql = (
            f"SELECT {keys},{aggregates},\n           {latency}\n"
            f"{spans}    GROUP BY {_positions(names)}"
        )
    else:
        sql = _by_bucket(keys, names, aggregates, _TOTALS, spans)
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


async def _covered(
    db: AsyncSession,
    statement: TextClause,
    params: dict[str, Any],
    project_id: uuid.UUID,
    groups: Groups,
    coverage: CoverageState | None,
) -> list[Row[Any]]:
    ranges = [window for group in groups for window in group]
    return await read_covered(
        db,
        statement,
        params,
        project_id,
        floor_hour(min(window.start for window in ranges)),
        max(window.end for window in ranges),
        coverage,
        raw=True,
        relevant_consumers=0,
    )


async def _read_span_kpis(
    db: AsyncSession,
    project_id: uuid.UUID,
    groups: Groups,
    environment: str | None,
    histogram: bool,
    coverage: CoverageState,
) -> list[Row[Any] | None]:
    rows: list[Row[Any] | None] = [None] * len(groups)
    params = _params(project_id, groups, environment, histogram)
    if params is not None:
        statement = _kpi_statement(environment is not None, histogram)
        for row in await _covered(db, statement, params, project_id, groups, coverage):
            rows[int(row.grp)] = row
    return rows


async def _read_trace_counts(
    db: AsyncSession,
    project_id: uuid.UUID,
    groups: Groups,
    environment: str | None,
    coverage: CoverageState,
) -> list[int]:
    counts = [0] * len(groups)
    params = _params(project_id, groups, environment)
    if params is not None:
        for row in await _covered(
            db, _TRACE_COUNTS[environment is not None], params, project_id, groups, coverage
        ):
            counts[int(row.grp)] = int(row.traces)
    return counts


async def _window_kpis(
    db: AsyncSession,
    project_id: uuid.UUID,
    window: TimeWindow,
    environment: str | None,
    split: datetime | None,
) -> tuple[KpisOut, bool]:
    coverage = CoverageState()
    plan = plan_window(window, split)
    estimated = plan.rollup is not None
    hours: list[HourlyRollup] = []
    if plan.rollup is not None:
        rollup = TimeWindow(*plan.rollup)
        hours = (await read_rollups(db, project_id, rollup, environment, coverage)).hours
    [spans] = await _read_span_kpis(db, project_id, [plan.raw], environment, estimated, coverage)
    [traces] = await _read_trace_counts(db, project_id, [plan.raw], environment, coverage)
    if coverage.unavailable:
        hours = (await read_rollups(db, project_id, window, environment, coverage)).hours
        plan = WindowPlan((floor_hour(window.start), window.end), ())
        spans, traces, estimated = None, 0, True
    result = _kpis(window.start, plan, hours, spans, traces, estimated)
    result.aggregate_status = coverage.status
    result.oldest_pending_at = coverage.oldest_pending_at
    if coverage.unavailable and not hours:
        for field in KpisOut.model_fields:
            if field not in {"aggregate_status", "oldest_pending_at"}:
                setattr(result, field, None)
    return result, estimated


async def overview(
    db: AsyncSession,
    project_id: uuid.UUID,
    window: TimeWindow,
    environment: str | None,
    split: datetime | None,
) -> OverviewOut:
    current, a = await _window_kpis(db, project_id, window, environment, split)
    previous, b = await _window_kpis(db, project_id, window.previous(), environment, split)
    return OverviewOut(current=current, previous=previous, approximate=a or b)

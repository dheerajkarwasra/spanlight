"""Raw-span reads behind alert metrics: one row of aggregates per requested group of ranges.

The definitions are the metrics endpoints' (``app.metrics.queries``): ``llm_calls``, errors and
latency percentiles are over spans of kind ``llm``; ``spans``, ``cost_usd`` and token totals are
over every kind, and ``cost_usd`` is NULL when no span was priced. The token and cost sums leave
usage rollups (``spans.usage_rollup``) out, as the hourly rollups do, so a budget never counts a
call twice; the counts keep them. The TTFT percentile reads only ``llm`` spans that measured a
time to first token.

A metric window's raw part is a group of up to two disjoint time ranges (the partial hours
around the rollup hours, see ``app.alerts.metrics``). Every range of a request is read by one
statement: the ranges are passed as three arrays (start, end, group) and joined to ``spans`` with
``unnest``, so each one is an index range scan, and the result is grouped by the group number.
Ranges of different groups may overlap; ranges of one group must not.

The statement is assembled from fixed fragments according to which filters are set, so values
only ever travel as bound parameters. ``traces`` is joined only for the filters that live on the
trace (``environment``, ``external_user_id``, ``metadata``, ``trace_name``): without them the
read stays on ``spans_project_started_cov_idx`` (see ``app.metrics.queries``). ``source_key_id``
is served by ``spans_project_source_key_started_idx`` and ``external_user_id`` by the partial
index of migration 0302; a ``metadata`` pair or ``trace_name`` is matched on the window's traces,
reached from the spans by primary key.

With an ``error_class`` or ``rate_limit_kind`` filter the row also counts ``class_errors``: the
failed llm calls of that class and kind, by the error rollup's own predicate
(``app.rollups.error_compute.FAILED_LLM_SPAN``, which leaves fault injections out), so the raw and
the rolled-up parts of a window count the same spans. Those two filters narrow only this count,
never ``llm_calls``, which a class error rate is divided by.

Percentiles are exact (``percentile_cont``). When the caller merges the raw part of a window with
hourly rollups it asks for a latency histogram as well, bucketed exactly as the rollup job does
(``app.rollups.compute``), so both halves add up element-wise.

The queries run in the caller's session and rely on its project binding for row-level security;
the explicit ``project_id`` filter is a second guard, not the only one.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from functools import cache
from typing import Any, Literal, get_args

from sqlalchemy import DateTime, Double, Integer, TextClause, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.types import MetricFilters
from app.api.window import TimeWindow
from app.core.model_match import like_prefix, model_filter_sql
from app.rollups.buckets import BUCKET_COUNT
from app.rollups.compute import BUCKET_INDEX_SQL, NEGATED_BOUNDS
from app.rollups.error_compute import FAILED_LLM_SPAN

LatencyColumn = Literal["duration_ms", "time_to_first_token_ms"]
"""The span column a percentile metric reads."""

_COUNTS = """
    count(*)                                                          AS spans,
    count(*) FILTER (WHERE s.kind = 'llm')                            AS llm_calls,
    count(*) FILTER (WHERE s.kind = 'llm' AND s.status = 'error')     AS llm_errors,
    sum(s.cost_usd) FILTER (WHERE NOT s.usage_rollup)                 AS cost_usd,
    coalesce(sum(s.input_tokens) FILTER (WHERE NOT s.usage_rollup), 0)  AS input_tokens,
    coalesce(sum(s.output_tokens) FILTER (WHERE NOT s.usage_rollup), 0) AS output_tokens
"""

_TRACE_JOIN = "JOIN traces AS t ON t.project_id = s.project_id AND t.trace_id = s.trace_id"

_CONDITIONS = {
    "environment": "t.environment = :environment",
    # A trace starts no later than its first span, so the bound drops no row and lets the
    # partial index of 0302 stop at the window's end.
    "external_user_id": "t.external_user_id = :external_user_id AND t.started_at < w.end_at",
    # Containment of one pair, the two parts bound as text: `jsonb_build_object` takes
    # arguments of any type, so an untyped bind cannot be resolved. The window bound is the
    # same one as above; `metadata` has no index (0800), the window's traces are read.
    "metadata": (
        "t.metadata @> jsonb_build_object(CAST(:metadata_key AS text), "
        "CAST(:metadata_value AS text)) AND t.started_at < w.end_at"
    ),
    "trace_name": "t.name = :trace_name AND t.started_at < w.end_at",
    "provider": "s.provider = :provider",
    "model": model_filter_sql("s.model"),
    "source_key_id": "s.source_key_id = :source_key_id",
}
_TRACE_FILTERS = frozenset({"environment", "external_user_id", "metadata", "trace_name"})
_ERROR_CONDITIONS = {
    "error_class": "s.error_class = :error_class",
    "rate_limit_kind": "s.rate_limit_kind = :rate_limit_kind",
}
_ERROR_FILTERS = frozenset(_ERROR_CONDITIONS)


@dataclass(frozen=True, slots=True)
class RawAggregate:
    """The raw-span aggregates of one window.

    ``percentile`` is the exact p95 of the requested column (``None`` without calls or when no
    column was requested); ``histogram`` is that column's llm histogram, when requested.
    ``class_errors`` counts the failed calls of the filtered error class and rate-limit kind, and
    is ``None`` when neither filter is set.
    """

    spans: int
    llm_calls: int
    llm_errors: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None
    percentile: float | None
    histogram: list[int] | None
    class_errors: int | None = None


EMPTY = RawAggregate(0, 0, 0, 0, 0, None, None, None)


def _histogram(column: str) -> str:
    """A 32-element ``integer[]`` counting the llm spans whose ``column`` is in each bucket.

    The NULL test is required: ``least`` ignores NULL arguments, so the index of a NULL value
    would be the last bucket, not NULL.
    """
    measured = f"s.kind = 'llm' AND s.{column} IS NOT NULL"
    index = BUCKET_INDEX_SQL.format(value=f"s.{column}")
    counts = ", ".join(
        f"count(*) FILTER (WHERE {measured} AND {index} = {position})"
        for position in range(BUCKET_COUNT)
    )
    return f"CAST(ARRAY[{counts}] AS integer[])"


def _class_errors(error_filters: frozenset[str]) -> str:
    """The count of failed llm calls matching every set error filter."""
    matches = "".join(f" AND {_ERROR_CONDITIONS[name]}" for name in sorted(error_filters))
    return f"count(*) FILTER (WHERE {FAILED_LLM_SPAN}{matches}) AS class_errors"


@cache
def _statement(
    filters: frozenset[str], latency: LatencyColumn | None, with_histogram: bool
) -> TextClause:
    """The statement for one combination of set filters and requested latency columns."""
    error_filters, dimensions = filters & _ERROR_FILTERS, filters - _ERROR_FILTERS
    columns = [_COUNTS, _class_errors(error_filters)] if error_filters else [_COUNTS]
    if latency is not None:
        columns.append(
            f"percentile_cont(0.95) WITHIN GROUP (ORDER BY s.{latency}) "
            "FILTER (WHERE s.kind = 'llm') AS percentile"
        )
        if with_histogram:
            columns.append(f"{_histogram(latency)} AS histogram")
    join = _TRACE_JOIN if dimensions & _TRACE_FILTERS else ""
    conditions = "".join(f"\n      AND {_CONDITIONS[name]}" for name in sorted(dimensions))
    statement = text(
        f"""
        SELECT w.grp, {", ".join(columns)}
        FROM unnest(
                 CAST(:starts AS timestamptz[]),
                 CAST(:ends AS timestamptz[]),
                 CAST(:groups AS integer[])
             ) AS w(start_at, end_at, grp)
        JOIN spans AS s
          ON s.project_id = :project_id
         AND s.started_at >= w.start_at
         AND s.started_at < w.end_at
        {join}
        WHERE TRUE{conditions}
        GROUP BY w.grp
        """
    ).bindparams(
        bindparam("starts", type_=ARRAY(DateTime(timezone=True))),
        bindparam("ends", type_=ARRAY(DateTime(timezone=True))),
        bindparam("groups", type_=ARRAY(Integer())),
    )
    if latency is not None and with_histogram:
        statement = statement.bindparams(bindparam("neg_bounds", type_=ARRAY(Double())))
    return statement


def _set_filters(filters: MetricFilters) -> dict[str, Any]:
    """The bound values of every set filter, by bind name. A metadata filter binds two."""
    values = {
        "environment": filters.environment,
        "external_user_id": filters.external_user_id,
        "trace_name": filters.trace_name,
        "provider": filters.provider,
        "model": filters.model,
        "source_key_id": filters.source_key_id,
        "error_class": filters.error_class,
        "rate_limit_kind": filters.rate_limit_kind,
    }
    if filters.metadata is not None:
        values["metadata_key"], values["metadata_value"] = filters.metadata
    return {name: value for name, value in values.items() if value is not None}


def _dimension_names(filters: MetricFilters) -> frozenset[str]:
    """The names of the set filters, as `_CONDITIONS` and `_ERROR_CONDITIONS` key them: the
    filter fields' names, which are not the bind names once a filter binds two values."""
    return frozenset(
        name for name in (*_CONDITIONS, *_ERROR_CONDITIONS) if getattr(filters, name) is not None
    )


async def read_window_aggregates(
    db: AsyncSession,
    project_id: uuid.UUID,
    groups: Sequence[Sequence[TimeWindow]],
    filters: MetricFilters,
    latency: LatencyColumn | None = None,
    with_histogram: bool = False,
) -> list[RawAggregate]:
    """Aggregates for each group of disjoint ranges, in order. A group without a non-empty range
    yields :data:`EMPTY`.

    Raises:
        ValueError: ``latency`` is not a span column this module reads; it is written into the
            SQL, so it is checked at run time, not only by the type checker.
    """
    if latency is not None and latency not in get_args(LatencyColumn):
        raise ValueError(f"unsupported latency column: {latency!r}")
    readable = [(g, r) for g, ranges in enumerate(groups) for r in ranges if r.start < r.end]
    narrows = filters.narrows_errors
    # A group without spans has no row: no failed calls of the class, not an unknown count.
    results = [replace(EMPTY, class_errors=0) if narrows else EMPTY] * len(groups)
    if not readable:
        return results
    set_filters = _set_filters(filters)
    params: dict[str, Any] = {
        **set_filters,
        **({"model_like": like_prefix(filters.model)} if filters.model is not None else {}),
        "project_id": project_id,
        "starts": [r.start for _, r in readable],
        "ends": [r.end for _, r in readable],
        "groups": [g for g, _ in readable],
    }
    with_histogram = with_histogram and latency is not None
    if with_histogram:
        params["neg_bounds"] = list(NEGATED_BOUNDS)
    statement = _statement(_dimension_names(filters), latency, with_histogram)
    for row in (await db.execute(statement, params)).all():
        results[int(row.grp)] = _aggregate(row, latency, with_histogram, narrows)
    return results


def _aggregate(
    row: Any, latency: LatencyColumn | None, with_histogram: bool, narrows: bool
) -> RawAggregate:
    return RawAggregate(
        spans=int(row.spans),
        llm_calls=int(row.llm_calls),
        llm_errors=int(row.llm_errors),
        input_tokens=int(row.input_tokens),
        output_tokens=int(row.output_tokens),
        cost_usd=row.cost_usd,
        percentile=row.percentile if latency is not None else None,
        histogram=[int(count) for count in row.histogram] if with_histogram else None,
        class_errors=int(row.class_errors) if narrows else None,
    )

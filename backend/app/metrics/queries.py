"""Raw-span reads behind the metrics endpoints, for windows of 24 hours or less.

Every statement has two forms, picked by whether an environment filter was given:

* Without a filter it reads ``spans`` alone. Every span has its trace (the composite foreign key
  guarantees it), so joining ``traces`` would neither add nor drop a row; it would only make the
  database read the whole of ``traces`` for the project, which at tens of millions of spans is
  the slowest part of the request.
* With a filter it joins ``traces``, because the environment belongs to the trace.

Both forms return the same rows for the same data. The filter-free form reads only the columns
that ``spans_project_started_cov_idx`` carries, so it is answered from the index without touching
the table (see migration 0016). Keep the two in step: a new column read here belongs in the index.
The one exception is ``usage_rollup`` (migration 0900), read to leave usage rollups out of the
token and cost sums (the counts keep them): the index is widened only if the load test shows a
heap fetch, as the Bet 4 spec decides.

The queries run in the caller's session and rely on its project binding for row-level security;
the explicit ``project_id`` filter is a second guard, not the only one.
"""

import uuid
from collections.abc import Sequence
from typing import Any, Literal, NamedTuple

from sqlalchemy import Row, String, TextClause, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.window import TimeWindow


class _Variants(NamedTuple):
    """One statement written twice: without and with an environment filter."""

    any_environment: TextClause
    one_environment: TextClause

    def pick(self, environment: str | None) -> TextClause:
        return self.any_environment if environment is None else self.one_environment


_SPANS_IN_WINDOW = """
    FROM spans AS s
    WHERE s.project_id = :project_id
      AND s.started_at >= :start
      AND s.started_at < :end
"""

_SPANS_IN_ENVIRONMENT = """
    FROM spans AS s
    JOIN traces AS t ON t.project_id = s.project_id AND t.trace_id = s.trace_id
    WHERE s.project_id = :project_id
      AND s.started_at >= :start
      AND s.started_at < :end
      AND t.environment = :environment
"""


def _span_statement(template: str) -> _Variants:
    """The statement for a template with a ``{spans}`` field, in both forms.

    The templates contain no other braces, so ``str.format`` only fills that field.
    """
    return _Variants(
        text(template.format(spans=_SPANS_IN_WINDOW)),
        text(template.format(spans=_SPANS_IN_ENVIRONMENT)).bindparams(
            bindparam("environment", type_=String)
        ),
    )


_SPAN_KPIS = _span_statement(
    """
    SELECT count(*) FILTER (WHERE s.kind = 'llm')                              AS llm_calls,
           count(*) FILTER (WHERE s.kind = 'llm' AND s.status = 'error')       AS llm_errors,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY s.duration_ms)
               FILTER (WHERE s.kind = 'llm')                                   AS p50_ms,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY s.duration_ms)
               FILTER (WHERE s.kind = 'llm')                                   AS p95_ms,
           sum(s.cost_usd) FILTER (WHERE NOT s.usage_rollup)                   AS cost_usd,
           count(*) FILTER (WHERE s.kind = 'llm' AND s.cost_usd IS NULL AND NOT s.usage_rollup)
                                                                               AS unpriced_calls,
           coalesce(sum(s.input_tokens) FILTER (WHERE NOT s.usage_rollup), 0)  AS input_tokens,
           coalesce(sum(s.output_tokens) FILTER (WHERE NOT s.usage_rollup), 0) AS output_tokens
    {spans}
    """
)

_TRACE_COUNT = _Variants(
    text(
        """
        SELECT count(*)
        FROM traces AS t
        WHERE t.project_id = :project_id
          AND t.started_at >= :start
          AND t.started_at < :end
        """
    ),
    text(
        """
        SELECT count(*)
        FROM traces AS t
        WHERE t.project_id = :project_id
          AND t.started_at >= :start
          AND t.started_at < :end
          AND t.environment = :environment
        """
    ).bindparams(bindparam("environment", type_=String)),
)

_TIMESERIES = _span_statement(
    """
    WITH buckets AS (
        SELECT generate_series(
                   date_trunc(:bucket, CAST(:start AS timestamptz), 'UTC') AT TIME ZONE 'UTC',
                   CAST(:end AS timestamptz) AT TIME ZONE 'UTC',
                   CAST(:step AS interval)
               ) AT TIME ZONE 'UTC' AS bucket_start
    ),
    aggregated AS (
        SELECT date_trunc(:bucket, s.started_at, 'UTC')                       AS bucket_start,
               count(*) FILTER (WHERE s.kind = 'llm')                          AS llm_calls,
               count(*) FILTER (WHERE s.kind = 'llm' AND s.status = 'error')   AS errors,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY s.duration_ms)
                   FILTER (WHERE s.kind = 'llm')                               AS p95_ms,
               sum(s.cost_usd) FILTER (WHERE NOT s.usage_rollup)               AS cost_usd,
               coalesce(sum(s.input_tokens) FILTER (WHERE NOT s.usage_rollup), 0)
                   + coalesce(sum(s.output_tokens) FILTER (WHERE NOT s.usage_rollup), 0)
                                                                               AS tokens
        {spans}
        GROUP BY 1
    )
    SELECT b.bucket_start,
           coalesce(a.llm_calls, 0) AS llm_calls,
           coalesce(a.errors, 0)    AS errors,
           a.p95_ms,
           a.cost_usd,
           coalesce(a.tokens, 0)    AS tokens
    FROM buckets AS b
    LEFT JOIN aggregated AS a ON a.bucket_start = b.bucket_start
    WHERE b.bucket_start < :end
    ORDER BY b.bucket_start
    """
)

_MODELS = _span_statement(
    """
    SELECT s.provider,
           s.model,
           count(*)                                                    AS calls,
           count(*) FILTER (WHERE s.status = 'error')                  AS errors,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY s.duration_ms)  AS p50_ms,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY s.duration_ms) AS p95_ms,
           coalesce(sum(s.input_tokens) FILTER (WHERE NOT s.usage_rollup), 0)  AS input_tokens,
           coalesce(sum(s.output_tokens) FILTER (WHERE NOT s.usage_rollup), 0) AS output_tokens,
           sum(s.cost_usd) FILTER (WHERE NOT s.usage_rollup)                   AS cost_usd
    {spans}
      AND s.kind = 'llm'
    GROUP BY s.provider, s.model
    ORDER BY calls DESC, s.provider NULLS LAST, s.model NULLS LAST
    LIMIT 100
    """
)


def _params(project_id: uuid.UUID, window: TimeWindow, environment: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {"project_id": project_id, "start": window.start, "end": window.end}
    if environment is not None:
        # Only the filtered form has the parameter.
        params["environment"] = environment
    return params


async def read_span_kpis(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow, environment: str | None
) -> Row[Any]:
    """One row of span aggregates for the window (see ``app.metrics.service`` for the meaning)."""
    params = _params(project_id, window, environment)
    return (await db.execute(_SPAN_KPIS.pick(environment), params)).one()


async def read_trace_count(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow, environment: str | None
) -> int:
    """The number of traces that started inside the window."""
    params = _params(project_id, window, environment)
    return int((await db.execute(_TRACE_COUNT.pick(environment), params)).scalar_one())


async def read_timeseries(
    db: AsyncSession,
    project_id: uuid.UUID,
    window: TimeWindow,
    environment: str | None,
    bucket: Literal["hour", "day"],
) -> Sequence[Row[Any]]:
    """One row per UTC bucket from the one holding ``start`` up to ``end``, empty ones included."""
    params = {**_params(project_id, window, environment), "bucket": bucket, "step": f"1 {bucket}"}
    return (await db.execute(_TIMESERIES.pick(environment), params)).all()


async def read_models(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow, environment: str | None
) -> Sequence[Row[Any]]:
    """The busiest provider and model pairs of the window, at most 100."""
    params = _params(project_id, window, environment)
    return (await db.execute(_MODELS.pick(environment), params)).all()

"""Raw-span reads behind the gateway overview. Nothing here writes.

The overview reads the spans a gateway key produced, never the whole project: every statement
filters `source_key_id IS NOT NULL`, which is the predicate of the partial index
`spans_project_gateway_started_idx` on `(project_id, started_at)` (migration 0202), so the
window is a range scan of that index and natively ingested spans are not visited. The window is
at most `MAX_OVERVIEW_WINDOW`, which keeps the read bounded by the gateway traffic of those days.

The gateway writes what the overview needs as span attributes (`app.gateway.tracing`), so the
numbers are read from `attributes` with a type check: a missing or non-numeric value is NULL and
does not count, never 0. Time to first token and the duration are span columns.

An `environment` filter applies to the trace the span belongs to, as for the metrics overview.
Each statement is written twice, without and with that join, and both return the same rows for
the same data. The queries run in the caller's session and rely on its project binding for
row-level security; the explicit `project_id` filter is a second guard, not the only one.
`provider_credentials` has no row-level security: the `org_id` condition is its boundary.
"""

import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Any, NamedTuple

from sqlalchemy import Row, String, TextClause, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.window import TimeWindow

MAX_OVERVIEW_WINDOW = timedelta(days=7)
MAX_TARGETS = 50
MAX_KEYS = 100


def _number(attribute: str) -> str:
    """SQL for a numeric span attribute: NULL when absent or not a JSON number."""
    return (
        f"CASE WHEN jsonb_typeof(s.attributes -> '{attribute}') = 'number' "
        f"THEN (s.attributes ->> '{attribute}')::numeric END"
    )


_FALLBACKS = _number("spanlight.gateway.fallbacks")
_ATTEMPTS = _number("spanlight.gateway.attempts")
_OVERHEAD_MS = _number("spanlight.gateway.overhead_ms")
_CACHE = "s.attributes ->> 'spanlight.cache'"
_TARGET = "s.attributes ->> 'spanlight.gateway.target'"
_PROVIDER = "s.attributes ->> 'spanlight.gateway.provider'"
_FAULT = "s.attributes ->> 'spanlight.fault.scenario'"

_GATEWAY_SPANS = """
    FROM spans AS s
    WHERE s.project_id = :project_id
      AND s.source_key_id IS NOT NULL
      AND s.started_at >= :start
      AND s.started_at < :end
"""

_GATEWAY_SPANS_IN_ENVIRONMENT = """
    FROM spans AS s
    JOIN traces AS t ON t.project_id = s.project_id AND t.trace_id = s.trace_id
    WHERE s.project_id = :project_id
      AND s.source_key_id IS NOT NULL
      AND s.started_at >= :start
      AND s.started_at < :end
      AND t.environment = :environment
"""


class _Variants(NamedTuple):
    """One statement written twice: without and with an environment filter."""

    any_environment: TextClause
    one_environment: TextClause

    def pick(self, environment: str | None) -> TextClause:
        return self.any_environment if environment is None else self.one_environment


def _statement(template: str) -> _Variants:
    """The statement for a template with a ``{spans}`` field, in both forms.

    The templates contain no other braces, so ``str.format`` only fills the named fields.
    """

    def fill(spans: str) -> str:
        return template.format(
            spans=spans,
            fallbacks=_FALLBACKS,
            attempts=_ATTEMPTS,
            overhead=_OVERHEAD_MS,
            cache=_CACHE,
            target=_TARGET,
            provider=_PROVIDER,
            fault=_FAULT,
        )

    return _Variants(
        text(fill(_GATEWAY_SPANS)),
        text(fill(_GATEWAY_SPANS_IN_ENVIRONMENT)).bindparams(
            bindparam("environment", type_=String)
        ),
    )


_TOTALS = _statement(
    """
    SELECT count(*)                                              AS requests,
           count(*) FILTER (WHERE s.status = 'error')            AS errors,
           count(*) FILTER (WHERE {cache} = 'hit')               AS cache_hits,
           count(*) FILTER (WHERE {cache} = 'miss')              AS cache_misses,
           coalesce(sum({fallbacks}), 0)::bigint                 AS fallbacks,
           coalesce(sum(greatest({attempts} - {fallbacks} - 1, 0)), 0)::bigint AS retries,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY ({overhead})::float8) AS p95_overhead_ms,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY s.time_to_first_token_ms) AS p95_ttft_ms,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY s.duration_ms - ({overhead})::float8)
               FILTER (WHERE {overhead} IS NOT NULL AND {attempts} > 0) AS p95_upstream_ms,
           count(*) FILTER (WHERE s.rate_limit_kind IS NOT NULL)  AS rate_limited
    {spans}
    """
)

# Clock-aligned buckets of `step` (whole hours, :00/:05/...), so the first and last may be partial.
_SERIES = _statement(
    """
    SELECT date_bin(:step, s.started_at, TIMESTAMPTZ '2001-01-01 00:00:00+00') AS bucket_start,
           count(*)                                              AS requests,
           count(*) FILTER (WHERE s.status = 'error')            AS errors
    {spans}
    GROUP BY 1
    """
)

# The credential's id is not on the span, only its name, which is unique within the organization
# and cannot be changed. The join recovers the id; it is NULL once the credential was deleted.
_BY_TARGET = _statement(
    """
    WITH per_target AS (
        SELECT {target}                                                AS target,
               max({provider})                                         AS provider,
               count(*)                                                AS requests,
               count(*) FILTER (WHERE s.status = 'error')              AS errors,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY s.duration_ms) AS p95_ms
        {spans}
          AND {target} IS NOT NULL
        GROUP BY 1
        ORDER BY requests DESC, target
        LIMIT {limit}
    )
    SELECT c.id AS credential_id, p.target AS credential_name, p.provider,
           p.requests, p.errors, p.p95_ms
    FROM per_target AS p
    LEFT JOIN provider_credentials AS c ON c.org_id = :org_id AND c.name = p.target
    ORDER BY p.requests DESC, p.target
    """.replace("{limit}", str(MAX_TARGETS))
)

_BY_KEY = _statement(
    """
    WITH per_key AS (
        SELECT s.source_key_id                                   AS key_id,
               count(*)                                          AS requests,
               count(*) FILTER (WHERE s.status = 'error')        AS errors,
               sum(s.cost_usd)                                   AS cost_usd
        {spans}
        GROUP BY s.source_key_id
        ORDER BY requests DESC, s.source_key_id
        LIMIT {limit}
    )
    SELECT p.key_id, k.name, k.environment, p.requests, p.errors, p.cost_usd
    FROM per_key AS p
    JOIN gateway_keys AS k ON k.id = p.key_id AND k.project_id = :project_id
    ORDER BY p.requests DESC, p.key_id
    """.replace("{limit}", str(MAX_KEYS))
)

_FAULTS = _statement(
    """
    SELECT {fault} AS scenario, count(*) AS calls
    {spans}
      AND {fault} IS NOT NULL
    GROUP BY 1
    ORDER BY calls DESC, scenario
    """
)


def _params(
    project_id: uuid.UUID, window: TimeWindow, environment: str | None, **extra: Any
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "project_id": project_id,
        "start": window.start,
        "end": window.end,
        **extra,
    }
    if environment is not None:
        # Only the filtered form has the parameter.
        params["environment"] = environment
    return params


async def read_totals(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow, environment: str | None
) -> Row[Any]:
    """One row of aggregates over every gateway call of the window."""
    params = _params(project_id, window, environment)
    return (await db.execute(_TOTALS.pick(environment), params)).one()


async def read_series(
    db: AsyncSession,
    project_id: uuid.UUID,
    window: TimeWindow,
    environment: str | None,
    step: timedelta,
) -> Sequence[Row[Any]]:
    """Requests and errors per non-empty bucket of `step`; empty buckets have no row."""
    params = _params(project_id, window, environment, step=step)
    return (await db.execute(_SERIES.pick(environment), params)).all()


async def read_by_target(
    db: AsyncSession,
    project_id: uuid.UUID,
    org_id: uuid.UUID,
    window: TimeWindow,
    environment: str | None,
) -> Sequence[Row[Any]]:
    """The busiest provider targets of the window, at most `MAX_TARGETS`, with credential ids."""
    params = _params(project_id, window, environment, org_id=org_id)
    return (await db.execute(_BY_TARGET.pick(environment), params)).all()


async def read_by_key(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow, environment: str | None
) -> Sequence[Row[Any]]:
    """The busiest gateway keys of the window, at most `MAX_KEYS`, with their names."""
    params = _params(project_id, window, environment)
    return (await db.execute(_BY_KEY.pick(environment), params)).all()


async def read_faults(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow, environment: str | None
) -> Sequence[Row[Any]]:
    """How many calls of the window carried a Lab fault, per scenario."""
    params = _params(project_id, window, environment)
    return (await db.execute(_FAULTS.pick(environment), params)).all()

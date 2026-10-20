"""Raw reads behind release comparison.

A release belongs to the trace. Every read starts from the project's traces that started inside
the window and carry a release (`traces_project_release_started_idx`), then joins their spans, so
a trace counts for the window it started in and all of its spans count with it.

The span side of each join is bounded below by the window start. That changes no result (a
trace starts when its first span does, so none of its spans starts earlier), but it lets the
planner read the spans through the covering index `spans_project_started_window_idx` by range
instead of probing the primary key once per trace. The upper side stays open: a span of a trace
that started before the window ends may start after it.

Compare reads take both releases at once (`release IN (:a, :b)`) and return one row per release
and group.

The queries run in the caller's session and rely on its project binding for row-level security;
the explicit `project_id` filter is a second guard, not the only one.
"""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Row, String, TextClause, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.window import TimeWindow

MAX_RELEASES = 200
MAX_MODELS = 100
MAX_NEW_ERRORS = 10
# Stored status messages are redacted but unbounded; only this many characters are normalised.
MESSAGE_PREFIX = 2000
MESSAGE_LENGTH = 200

_ENVIRONMENT = "AND t.environment = :environment"
_TWO_RELEASES = "AND t.release IN (:a, :b)"

_STATS = """
    WITH scoped AS (
        SELECT t.trace_id, t.release, t.started_at
        FROM traces AS t
        WHERE t.project_id = :project_id
          AND t.started_at >= :start
          AND t.started_at < :end
          AND t.release IS NOT NULL
          {releases}
          {environment}
    ),
    seen AS (
        SELECT release,
               count(*)          AS traces,
               min(started_at)   AS first_seen_at,
               max(started_at)   AS last_seen_at
        FROM scoped
        GROUP BY release
        ORDER BY max(started_at) DESC, release
        LIMIT :limit
    )
    SELECT r.release, r.first_seen_at, r.last_seen_at, r.traces,
           count(*) FILTER (WHERE s.kind = 'llm')                                AS llm_calls,
           count(*) FILTER (WHERE s.kind = 'llm' AND s.cost_usd IS NULL)         AS unpriced_calls,
           count(*) FILTER (WHERE s.kind = 'llm' AND s.status = 'error')         AS llm_errors,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY s.duration_ms)
               FILTER (WHERE s.kind = 'llm')                                     AS p50_ms,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY s.duration_ms)
               FILTER (WHERE s.kind = 'llm')                                     AS p95_ms,
           sum(s.cost_usd)                                                       AS cost_usd,
           coalesce(sum(s.input_tokens), 0)                                      AS input_tokens,
           coalesce(sum(s.output_tokens), 0)                                     AS output_tokens
    FROM seen AS r
    JOIN scoped AS c ON c.release = r.release
    LEFT JOIN spans AS s
           ON s.project_id = :project_id AND s.trace_id = c.trace_id AND s.started_at >= :start
    GROUP BY r.release, r.first_seen_at, r.last_seen_at, r.traces
    ORDER BY r.last_seen_at DESC, r.release
"""

# The spans of the two compared releases, for the three reads below.
_SPANS_OF_RELEASES = """
    FROM traces AS t
    JOIN spans AS s ON s.project_id = t.project_id AND s.trace_id = t.trace_id
    WHERE t.project_id = :project_id
      AND t.started_at >= :start
      AND t.started_at < :end
      AND t.release IN (:a, :b)
      AND s.started_at >= :start
      {environment}
"""

_MODELS = f"""
    WITH counted AS (
        SELECT t.release, s.model, count(*) AS calls
        {_SPANS_OF_RELEASES}
          AND s.kind = 'llm'
        GROUP BY t.release, s.model
    ),
    ranked AS (
        SELECT *, row_number() OVER (PARTITION BY release ORDER BY calls DESC, model) AS rn
        FROM counted
    )
    SELECT release, model, calls FROM ranked WHERE rn <= {MAX_MODELS}
"""

_ERROR_CLASSES = f"""
    SELECT t.release, s.error_class, count(*) AS n
    {_SPANS_OF_RELEASES}
      AND s.status = 'error'
    GROUP BY t.release, s.error_class
"""

# Failure messages with the parts that differ on every occurrence removed, grouped in the
# database so the "absent in a" test sees every failed span and not a sample. The patterns are
# POSIX ARE (`\y` is a word boundary); they are one rule with the wording in
# docs/api-deviations.md: UUIDs, hex ids of 12 or more digits and letter-and-digit runs of 16 or
# more characters (provider ids such as `req_011CgB…`, whose prefix is kept) become `…`, other
# digit runs `#`. No `(?:` in the patterns: SQLAlchemy would read `:0x` as a bind parameter.
_NEW_ERRORS = r"""
    WITH failed AS (
        SELECT t.release, s.trace_id,
               left(btrim(regexp_replace(regexp_replace(regexp_replace(regexp_replace(
                   regexp_replace(
                   left(s.status_message, {prefix}),
                   '\y[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\y',
                   '…', 'gi'),
                   '\y(0x)?[0-9a-f]{12,}\y', '…', 'gi'),
                   '(?<![a-z0-9])(?=[a-z]*[0-9])(?=[0-9]*[a-z])[a-z0-9]{16,}(?![a-z0-9])',
                   '…', 'gi'),
                   '[0-9]+', '#', 'g'),
                   '\s+', ' ', 'g')), {length}) AS message
        {spans}
          AND s.status = 'error'
          AND s.status_message IS NOT NULL
    )
    SELECT message,
           count(*) FILTER (WHERE release = :b)       AS n,
           min(trace_id) FILTER (WHERE release = :b)  AS example_trace_id
    FROM failed
    GROUP BY message
    HAVING count(*) FILTER (WHERE release = :a) = 0
       AND count(*) FILTER (WHERE release = :b) > 0
       AND message <> ''
    ORDER BY n DESC, message
    LIMIT {limit}
"""


def _statement(template: str, *, environment: str | None, pair: bool = False) -> TextClause:
    """The template with its optional `{environment}` and `{releases}` clauses filled in.

    Plain replacement, not `str.format`: the patterns in `_NEW_ERRORS` contain braces.
    """
    sql = template.replace("{environment}", _ENVIRONMENT if environment is not None else "")
    sql = sql.replace("{releases}", _TWO_RELEASES if pair else "")
    names = (["environment"] if environment is not None else []) + (["a", "b"] if pair else [])
    clause = text(sql)
    return (
        clause.bindparams(*(bindparam(name, type_=String) for name in names)) if names else clause
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
        params["environment"] = environment
    return params


async def read_release_stats(
    db: AsyncSession,
    project_id: uuid.UUID,
    window: TimeWindow,
    environment: str | None,
    *,
    only: tuple[str, str] | None = None,
) -> Sequence[Row[Any]]:
    """Per-release aggregates, newest first: all (at most 200), or just the two in `only`."""
    statement = _statement(_STATS, environment=environment, pair=only is not None)
    params = _params(project_id, window, environment, limit=MAX_RELEASES)
    if only is not None:
        params |= {"a": only[0], "b": only[1]}
    return (await db.execute(statement, params)).all()


async def read_model_counts(
    db: AsyncSession,
    project_id: uuid.UUID,
    window: TimeWindow,
    environment: str | None,
    pair: tuple[str, str],
) -> Sequence[Row[Any]]:
    """LLM calls by model of two releases (each one's 100 busiest), as (release, model, calls)."""
    statement = _statement(_MODELS, environment=environment, pair=True)
    params = _params(project_id, window, environment, a=pair[0], b=pair[1])
    return (await db.execute(statement, params)).all()


async def read_error_classes(
    db: AsyncSession,
    project_id: uuid.UUID,
    window: TimeWindow,
    environment: str | None,
    pair: tuple[str, str],
) -> Sequence[Row[Any]]:
    """Failed spans of two releases by error class, as (release, error_class, n)."""
    statement = _statement(_ERROR_CLASSES, environment=environment, pair=True)
    params = _params(project_id, window, environment, a=pair[0], b=pair[1])
    return (await db.execute(statement, params)).all()


async def read_new_errors(
    db: AsyncSession,
    project_id: uuid.UUID,
    window: TimeWindow,
    environment: str | None,
    pair: tuple[str, str],
) -> Sequence[Row[Any]]:
    """The 10 most frequent normalised failure messages of `pair[1]` that `pair[0]` never has."""
    spans = _SPANS_OF_RELEASES
    template = _NEW_ERRORS.replace("{spans}", spans)
    template = template.replace("{prefix}", str(MESSAGE_PREFIX))
    template = template.replace("{length}", str(MESSAGE_LENGTH))
    template = template.replace("{limit}", str(MAX_NEW_ERRORS))
    statement = _statement(template, environment=environment, pair=True)
    params = _params(project_id, window, environment, a=pair[0], b=pair[1])
    return (await db.execute(statement, params)).all()

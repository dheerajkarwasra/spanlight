"""Recompute the per-user daily stats of a project from its raw traces and spans.

A trace counts for the UTC day it started and an end user is the trace's `external_user_id`.
The figures of a trace are those of its LLM spans, whatever day they started: `llm_calls` and
`errors` count them (an error is a span with status `error`), `unpriced_calls` counts those
without a cost, `tokens` adds input and output
tokens (input already includes the cached ones) and `cost_usd` sums the priced ones, `NULL` when
the user's day has no priced call. `unpriced_calls`, `tokens` and `cost_usd` leave usage rollups
(`spans.usage_rollup`, a span repeating a descendant's usage) out; `llm_calls` and `errors` keep
them. `computed_at` is the time of the recompute that last wrote the
row (`now()`, the transaction's start), whether or not its figures changed.

The recompute is idempotent. Rows of the range are upserted on their natural key, and rows of
the range no trace backs any more (a trace retention removed, say) are deleted, so the table
never keeps a stale day. The statement runs in the caller's session, which must be bound to the
project (row-level security); the caller commits.
"""

import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.end_users.days import ONE_DAY, midnight

# One statement, so the aggregate is computed once for both writes. `fresh` is the traces of the
# range, each with its LLM spans (the span side is bounded below by the range start: a trace
# starts when its first span does, so none of its spans starts earlier). The upsert and the
# delete touch disjoint rows: the delete only takes rows `fresh` does not have.
_REFRESH = text(
    """
    WITH fresh AS (
        SELECT (t.started_at AT TIME ZONE 'UTC')::date                      AS day,
               t.external_user_id,
               count(DISTINCT t.trace_id)                                    AS traces,
               count(s.span_id)                                              AS llm_calls,
               count(s.span_id) FILTER (WHERE s.status = 'error')            AS errors,
               count(s.span_id) FILTER (WHERE s.cost_usd IS NULL AND NOT s.usage_rollup)
                                                                             AS unpriced_calls,
               sum(s.cost_usd) FILTER (WHERE NOT s.usage_rollup)             AS cost_usd,
               coalesce(sum(coalesce(s.input_tokens, 0) + coalesce(s.output_tokens, 0))
                            FILTER (WHERE NOT s.usage_rollup), 0)            AS tokens
        FROM traces AS t
        LEFT JOIN spans AS s
               ON s.project_id = t.project_id
              AND s.trace_id = t.trace_id
              AND s.kind = 'llm'
              AND s.started_at >= :start
        WHERE t.project_id = :project_id
          AND t.external_user_id IS NOT NULL
          AND t.started_at >= :start
          AND t.started_at < :end
        GROUP BY 1, 2
    ),
    upserted AS (
        INSERT INTO user_stats_daily
            (project_id, day, external_user_id, traces, llm_calls, errors, unpriced_calls,
             cost_usd, tokens, computed_at)
        SELECT CAST(:project_id AS uuid), fresh.day, fresh.external_user_id, fresh.traces,
               fresh.llm_calls, fresh.errors, fresh.unpriced_calls, fresh.cost_usd, fresh.tokens,
               now()
        FROM fresh
        WHERE true
        ON CONFLICT (project_id, day, external_user_id) DO UPDATE
        SET traces = EXCLUDED.traces,
            llm_calls = EXCLUDED.llm_calls,
            errors = EXCLUDED.errors,
            unpriced_calls = EXCLUDED.unpriced_calls,
            cost_usd = EXCLUDED.cost_usd,
            tokens = EXCLUDED.tokens,
            computed_at = now()
        RETURNING 1
    ),
    removed AS (
        DELETE FROM user_stats_daily AS u
        WHERE u.project_id = :project_id
          AND u.day >= :first_day AND u.day <= :last_day
          AND NOT EXISTS (
              SELECT 1 FROM fresh
              WHERE fresh.day = u.day AND fresh.external_user_id = u.external_user_id
          )
        RETURNING 1
    )
    SELECT (SELECT count(*) FROM upserted) AS written, (SELECT count(*) FROM removed) AS removed
    """
)


async def refresh_user_stats(db: AsyncSession, project_id: uuid.UUID, days: Sequence[date]) -> int:
    """Recompute the stats of the days from the earliest to the latest in `days`.

    Returns how many rows were written (inserted or updated). Does nothing for no days.
    """
    if not days:
        return 0
    first_day, last_day = min(days), max(days)
    params = {
        "project_id": project_id,
        "start": midnight(first_day),
        "end": midnight(last_day + ONE_DAY),
    }
    row = (
        await db.execute(_REFRESH, {**params, "first_day": first_day, "last_day": last_day})
    ).one()
    return int(row.written)

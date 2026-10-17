"""Recompute a project's daily cost rollups from its LLM spans (spec §6.8, §10.3).

One row per UTC day (the day a span started), provider, model and service tier. Tokens are split
into the billable classes exactly as `PriceBook.cost` splits them (`app.pricing.price_row`):
cache reads are capped at the prompt, cache writes at what is left of it, the 1-hour writes at
the writes, and the rest of the prompt is uncached. Calls are counted by what happened to their
price:

* `priced_calls` have a cost; `unpriced_calls` have a model and both token counts but no cost
  (no price matched).
* `usage_unreported_calls` have no output count and one of the six `usage.unreported_reason`
  values; `cut_stream_calls` are those whose reason is a cut stream. Their cost is unknown, never
  cheap: they are neither priced nor unpriced.
* `cache_hit_calls` were answered from the gateway cache.
* `unmodelled_modifier_calls` carry a pricing modifier Spanlight does not price: the span says so
  (`usage.pricing_modifier_unknown`) and has usage (a call that failed before any usage came back
  was never priced at any rate, whatever wrote the attribute), or the row's tier has no factor in
  the snapshot in force at the start of its day and is not commitment-billed (OpenAI priority), in
  which case every call of the row counts once.

`cost_usd` sums the span costs (fees included) and `fee_usd` the fees of those same spans, so
`cost_usd - fee_usd` is exactly the token part; each is NULL when no span of the row has one. A
fee on a span whose cost is unknown (a cut stream that counted web searches) is in neither, the
way the trace total leaves it out: Spanlight did not record it, and the report shows the provider's
fee as unexplained rather than half-explained. Token and count columns are 0 when nothing was
reported. Every token and cost sum leaves usage rollups (`spans.usage_rollup`, a span repeating a
descendant's usage) out, so a call is billed once; the call counts keep them.

`provider_canonical` is the provider after the alias table and `model_pattern` the longest seed
or override pattern matching the model name, priced or not (`mapping_patterns`), the same rule the
provider lines are mapped with, so both sides group a model alike.

The recompute is idempotent. Rows of the range are upserted on the natural key (their ids stay),
and rows of the range no span backs any more are deleted. It runs in the caller's session, which
must be bound to the project (row-level security); the caller commits. Refreshes of one project
run one at a time (an advisory lock held until the caller's commit), and a project deleted
meanwhile is skipped.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.end_users.days import ONE_DAY, midnight
from app.pricing.provider_alias import canonical_provider
from app.pricing.tiers import multipliers_for
from app.pricing.unreported import CUT_STREAM_REASONS, UNREPORTED_REASONS
from app.reconciliation.line_items import model_pattern_for
from app.reconciliation.queries import mapping_patterns

# One statement, so the aggregate is computed once for both writes. `classed` is the range's LLM
# spans with their tokens split into classes; a blank provider or model groups with the unknown
# one, which the natural key reads as the empty string anyway. The upsert and the delete touch
# disjoint rows: the delete only takes rows `fresh` does not have, and runs to completion though
# the final select does not read it (Postgres runs every data-modifying CTE). The pass after the
# statement sets `provider_canonical` and `model_pattern`; an insert leaves them NULL until then,
# inside the same transaction.
_REFRESH = text(
    """
    WITH classed AS (
        SELECT (s.started_at AT TIME ZONE 'UTC')::date AS day,
               nullif(s.provider, '') AS provider,
               nullif(s.model, '') AS model,
               s.service_tier,
               s.input_tokens, s.output_tokens, s.cost_usd, s.fee_usd, s.web_search_requests,
               s.attributes, s.usage_rollup,
               r.reads, w.writes, w1.writes_1h,
               i.input - r.reads - w.writes AS uncached
        FROM spans AS s
        CROSS JOIN LATERAL (SELECT coalesce(s.input_tokens, 0) AS input) AS i
        CROSS JOIN LATERAL (SELECT least(coalesce(s.cached_tokens, 0), i.input) AS reads) AS r
        CROSS JOIN LATERAL (
            SELECT least(coalesce(s.cache_write_tokens, 0), i.input - r.reads) AS writes
        ) AS w
        CROSS JOIN LATERAL (
            SELECT least(coalesce(s.cache_write_1h_tokens, 0), w.writes) AS writes_1h
        ) AS w1
        WHERE s.project_id = :project_id
          AND s.kind = 'llm'
          AND s.started_at >= :start
          AND s.started_at < :end
    ),
    fresh AS (
        SELECT day, provider, model, service_tier,
               count(*) AS calls,
               count(*) FILTER (WHERE cost_usd IS NOT NULL) AS priced_calls,
               count(*) FILTER (
                   WHERE cost_usd IS NULL AND model IS NOT NULL
                     AND input_tokens IS NOT NULL AND output_tokens IS NOT NULL
               ) AS unpriced_calls,
               count(*) FILTER (
                   WHERE output_tokens IS NULL
                     AND attributes->>'usage.unreported_reason' = ANY(:unreported)
               ) AS usage_unreported_calls,
               count(*) FILTER (
                   WHERE output_tokens IS NULL
                     AND attributes->>'usage.unreported_reason' = ANY(:cut_stream)
               ) AS cut_stream_calls,
               count(*) FILTER (WHERE attributes->>'spanlight.cache' = 'hit') AS cache_hit_calls,
               count(*) FILTER (
                   WHERE attributes ? 'usage.pricing_modifier_unknown'
                     AND input_tokens IS NOT NULL
               ) AS unmodelled_modifier_calls,
               coalesce(sum(uncached) FILTER (WHERE NOT usage_rollup), 0)
                   AS uncached_input_tokens,
               coalesce(sum(reads) FILTER (WHERE NOT usage_rollup), 0) AS cache_read_tokens,
               coalesce(sum(writes - writes_1h) FILTER (WHERE NOT usage_rollup), 0)
                   AS cache_write_5m_tokens,
               coalesce(sum(writes_1h) FILTER (WHERE NOT usage_rollup), 0)
                   AS cache_write_1h_tokens,
               coalesce(sum(output_tokens) FILTER (WHERE NOT usage_rollup), 0) AS output_tokens,
               coalesce(sum(web_search_requests) FILTER (WHERE NOT usage_rollup), 0)
                   AS web_search_requests,
               sum(cost_usd) FILTER (WHERE NOT usage_rollup) AS cost_usd,
               sum(fee_usd) FILTER (WHERE cost_usd IS NOT NULL AND NOT usage_rollup) AS fee_usd
        FROM classed
        GROUP BY day, provider, model, service_tier
    ),
    upserted AS (
        INSERT INTO cost_rollups_daily
            (project_id, day, provider, model, service_tier, calls, priced_calls, unpriced_calls,
             usage_unreported_calls, cut_stream_calls, cache_hit_calls, unmodelled_modifier_calls,
             uncached_input_tokens, cache_read_tokens, cache_write_5m_tokens,
             cache_write_1h_tokens, output_tokens, web_search_requests, cost_usd, fee_usd,
             computed_at)
        SELECT CAST(:project_id AS uuid), day, provider, model, service_tier, calls,
               priced_calls, unpriced_calls, usage_unreported_calls, cut_stream_calls,
               cache_hit_calls, unmodelled_modifier_calls, uncached_input_tokens,
               cache_read_tokens, cache_write_5m_tokens, cache_write_1h_tokens, output_tokens,
               web_search_requests, cost_usd, fee_usd, now()
        FROM fresh
        WHERE true
        ON CONFLICT (project_id, day, coalesce(provider, ''), coalesce(model, ''),
                     coalesce(service_tier, ''))
        DO UPDATE SET
            calls = EXCLUDED.calls,
            priced_calls = EXCLUDED.priced_calls,
            unpriced_calls = EXCLUDED.unpriced_calls,
            usage_unreported_calls = EXCLUDED.usage_unreported_calls,
            cut_stream_calls = EXCLUDED.cut_stream_calls,
            cache_hit_calls = EXCLUDED.cache_hit_calls,
            unmodelled_modifier_calls = EXCLUDED.unmodelled_modifier_calls,
            uncached_input_tokens = EXCLUDED.uncached_input_tokens,
            cache_read_tokens = EXCLUDED.cache_read_tokens,
            cache_write_5m_tokens = EXCLUDED.cache_write_5m_tokens,
            cache_write_1h_tokens = EXCLUDED.cache_write_1h_tokens,
            output_tokens = EXCLUDED.output_tokens,
            web_search_requests = EXCLUDED.web_search_requests,
            cost_usd = EXCLUDED.cost_usd,
            fee_usd = EXCLUDED.fee_usd,
            computed_at = now()
        RETURNING id, day, provider, model, service_tier, calls, unmodelled_modifier_calls
    ),
    removed AS (
        DELETE FROM cost_rollups_daily AS c
        WHERE c.project_id = :project_id
          AND c.day >= :first_day AND c.day <= :last_day
          AND NOT EXISTS (
              SELECT 1 FROM fresh
              WHERE fresh.day = c.day
                AND coalesce(fresh.provider, '') = coalesce(c.provider, '')
                AND coalesce(fresh.model, '') = coalesce(c.model, '')
                AND coalesce(fresh.service_tier, '') = coalesce(c.service_tier, '')
          )
        RETURNING 1
    )
    SELECT * FROM upserted
    """
)

_LABEL = text(
    """
    UPDATE cost_rollups_daily
    SET provider_canonical = :provider_canonical,
        model_pattern = :model_pattern,
        unmodelled_modifier_calls = :unmodelled_modifier_calls
    WHERE id = :id
    """
)

# Two refreshes of one project (the 15-minute and the nightly pass on two workers, or a pass and
# the report's lazy build) would upsert the same rows in different orders and could deadlock. A
# transaction-level advisory lock keyed on the project makes the second wait for the first to
# commit, then recompute from the committed data (`app.rollups.compute` does the same).
_LOCK_PROJECT = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")

# A shared lock on the project row, taken before any rollup row is touched. The upsert's foreign
# key check takes it anyway, but only after the statement holds rollup rows that a project or
# organization deletion cascades into: the deletion would hold the project and wait for the rows,
# this refresh the rows and wait for the project. Taken first, a running deletion makes the refresh
# wait and then find no row, and a running refresh makes the deletion wait for it.
_ORG_OF_PROJECT = text("SELECT org_id FROM projects WHERE id = :project_id FOR KEY SHARE")


@dataclass(frozen=True, slots=True)
class WrittenRow:
    """What the pass after the upsert needs of one written row."""

    id: int
    day: date
    provider: str | None
    model: str | None
    service_tier: str | None
    calls: int
    unmodelled_modifier_calls: int


async def refresh_cost_rollups(
    db: AsyncSession, project_id: uuid.UUID, days: Sequence[date]
) -> int:
    """Recompute the rollups of the days from the earliest to the latest in `days`.

    Returns how many rows were written (inserted or updated). Does nothing for no days, or for a
    project that no longer exists.
    """
    if not days:
        return 0
    await db.execute(_LOCK_PROJECT, {"key": f"cost_rollups:{project_id}"})
    org_id = (await db.execute(_ORG_OF_PROJECT, {"project_id": project_id})).scalar_one_or_none()
    if org_id is None:
        return 0
    first_day, last_day = min(days), max(days)
    params = {
        "project_id": project_id,
        "start": midnight(first_day),
        "end": midnight(last_day + ONE_DAY),
        "first_day": first_day,
        "last_day": last_day,
        "unreported": list(UNREPORTED_REASONS),
        "cut_stream": list(CUT_STREAM_REASONS),
    }
    result = await db.execute(_REFRESH, params)
    written = [
        WrittenRow(
            id=row.id,
            day=row.day,
            provider=row.provider,
            model=row.model,
            service_tier=row.service_tier,
            calls=row.calls,
            unmodelled_modifier_calls=row.unmodelled_modifier_calls,
        )
        for row in result
    ]
    if written:
        patterns = await mapping_patterns(db, org_id)
        await db.execute(_LABEL, [label(row, patterns) for row in written])
    return len(written)


def label(row: WrittenRow, patterns: Sequence[str]) -> dict[str, object]:
    """The canonical provider, the model's pattern and the unmodelled-modifier count of a row.

    A tier the snapshot in force at the start of the row's day has no factor for (and that is not
    commitment-billed) is priced at the standard rate, so every call of the row carries an
    unmodelled modifier: the count is `calls`, which already includes any call the span flagged.
    """
    provider = canonical_provider(row.provider)
    note = multipliers_for(None, midnight(row.day)).note(provider, row.service_tier)
    unmodelled = row.calls if note == "unmodelled" else row.unmodelled_modifier_calls
    return {
        "id": row.id,
        "provider_canonical": provider,
        "model_pattern": model_pattern_for(row.model, patterns),
        "unmodelled_modifier_calls": unmodelled,
    }

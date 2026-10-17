"""Recompute a project's daily attribution rollups from its raw traces and spans (spec §5.4).

One statement per (kind, key) recomputes a range of UTC days: the `grouped` CTE of
`app.attribution.aggregate_sql` sums the range's traces per (day, environment, value), then the
values of each (day, environment) are ranked `cost_usd DESC NULLS LAST, llm_calls DESC,
group_value ASC`. The first `VALUE_CAP` are written as `bucket = 'value'`; the rest are summed
into one `other` row whose `folded_values` counts them, with the money sums under the same
$1,000,000 guard as every stored cost (`money_sum`, `retry_cost_sum`), so a fold of $1M or more
reads NULL instead of failing the refresh (`numeric(14, 8)`). The traces without the dimension
are the `none` row. Rows are upserted on the natural key (`UNIQUE NULLS NOT DISTINCT`, so a
NULL environment updates in place) and the rows of the range the fresh set lacks are deleted, so
a value that moved into `other`, or a key that lost every trace, leaves no stale row.

The recompute is idempotent: replaying it yields the same rows and the same figures. It runs in
the caller's session, which must be bound to the project (row-level security); the caller
commits. `refresh_attribution` runs every kind and every promoted key of a project, backfills a
newly promoted key over its own days, and deletes the `metadata` rows of keys no longer promoted.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.attribution.aggregate_sql import GroupKind, grouped_cte, money_sum, retry_cost_sum
from app.core.observability import ATTRIBUTION_FOLDED_VALUES
from app.end_users.days import ONE_DAY, midnight

# Distinct values kept per (project, day, environment, kind, key); the rest fold into `other`.
VALUE_CAP = 1000

_FIGURES = (
    "traces",
    "llm_calls",
    "errors",
    "unpriced_calls",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "retries",
    "retry_cost_usd",
)
_COLUMNS = ("day", "environment", "bucket", "group_value", *_FIGURES, "folded_values")
_UPDATES = ",\n            ".join(
    f"{column} = EXCLUDED.{column}" for column in (*_FIGURES, "folded_values")
)
_NATURAL_KEY = "project_id, group_kind, group_key, environment, day, bucket, group_value"

# `fresh` is the range's rows as they should be stored. The upsert and the delete touch disjoint
# rows (the delete only takes rows `fresh` does not have), and every data-modifying CTE runs to
# completion whether or not the final select reads it.
_REFRESH = """
    WITH {grouped},
    ranked AS (
        SELECT grouped.*,
               row_number() OVER (
                   PARTITION BY day, environment
                   ORDER BY cost_usd DESC NULLS LAST, llm_calls DESC, group_value ASC
               ) AS rank
        FROM grouped
        WHERE group_value IS NOT NULL
    ),
    fresh AS (
        SELECT day, environment, 'value' AS bucket, group_value,
               traces, llm_calls, errors, unpriced_calls, input_tokens, output_tokens,
               cost_usd, retries, retry_cost_usd, CAST(NULL AS integer) AS folded_values
        FROM ranked
        WHERE rank <= :value_cap
        UNION ALL
        SELECT day, environment, 'none', NULL,
               traces, llm_calls, errors, unpriced_calls, input_tokens, output_tokens,
               cost_usd, retries, retry_cost_usd, NULL
        FROM grouped
        WHERE group_value IS NULL
        UNION ALL
        SELECT day, environment, 'other', NULL,
               sum(traces), sum(llm_calls), sum(errors), sum(unpriced_calls),
               sum(input_tokens), sum(output_tokens),
               {cost}, sum(retries), {retry_cost}, count(*)
        FROM ranked
        WHERE rank > :value_cap
        GROUP BY day, environment
    ),
    upserted AS (
        INSERT INTO attribution_rollups_daily
            (project_id, group_kind, group_key, {columns}, computed_at)
        SELECT CAST(:project_id AS uuid), CAST(:kind AS text), CAST(:key AS text),
               {fresh_columns}, now()
        FROM fresh
        ON CONFLICT ({natural_key}) DO UPDATE
        SET {updates},
            computed_at = now()
        RETURNING 1
    ),
    removed AS (
        DELETE FROM attribution_rollups_daily AS r
        WHERE r.project_id = :project_id
          AND r.group_kind = :kind
          AND r.group_key IS NOT DISTINCT FROM CAST(:key AS text)
          AND r.day >= :first_day AND r.day <= :last_day
          AND NOT EXISTS (
              SELECT 1 FROM fresh
              WHERE fresh.day = r.day
                AND fresh.environment IS NOT DISTINCT FROM r.environment
                AND fresh.bucket = r.bucket
                AND fresh.group_value IS NOT DISTINCT FROM r.group_value
          )
        RETURNING 1
    )
    SELECT (SELECT count(*) FROM upserted) AS written,
           (SELECT count(*) FROM removed) AS removed,
           (SELECT coalesce(sum(folded_values), 0) FROM fresh WHERE bucket = 'other') AS folded
"""

_STATEMENTS = {
    kind: text(
        _REFRESH.format(
            grouped=grouped_cte(kind, by_day=True, with_environment=False),
            cost=money_sum("cost_usd"),
            retry_cost=retry_cost_sum("retries", "retry_cost_usd"),
            columns=", ".join(_COLUMNS),
            fresh_columns=", ".join(f"fresh.{column}" for column in _COLUMNS),
            natural_key=_NATURAL_KEY,
            updates=_UPDATES,
        )
    )
    for kind in ("metadata", "trace_name", "user", "release")
}

# The `metadata` rows of keys the project no longer promotes.
_PRUNE_KEYS = text(
    """
    DELETE FROM attribution_rollups_daily
    WHERE project_id = :project_id
      AND group_kind = 'metadata'
      AND group_key <> ALL (CAST(:promoted AS text[]))
    """
)

# The promoted keys with any row on or after `:since`.
_KEYS_WITH_ROWS = text(
    """
    SELECT DISTINCT group_key
    FROM attribution_rollups_daily
    WHERE project_id = :project_id
      AND group_kind = 'metadata'
      AND group_key = ANY (CAST(:promoted AS text[]))
      AND day >= :since
    """
)


@dataclass(frozen=True, slots=True)
class RefreshResult:
    """Rows written (inserted or updated), rows removed, and values folded into `other`."""

    written: int = 0
    removed: int = 0
    folded: int = 0

    def __add__(self, other: "RefreshResult") -> "RefreshResult":
        return RefreshResult(
            self.written + other.written,
            self.removed + other.removed,
            self.folded + other.folded,
        )


async def refresh_group(
    db: AsyncSession,
    project_id: uuid.UUID,
    kind: GroupKind,
    key: str | None,
    days: Sequence[date],
    *,
    value_cap: int = VALUE_CAP,
) -> RefreshResult:
    """Recompute one kind (and, for `metadata`, one key) from the earliest to the latest day.

    Does nothing for no days. Counts the values it folded on the folded-values metric.
    """
    if not days:
        return RefreshResult()
    first_day, last_day = min(days), max(days)
    params = {
        "project_id": project_id,
        "kind": kind,
        "key": key,
        "start": midnight(first_day),
        "end": midnight(last_day + ONE_DAY),
        "first_day": first_day,
        "last_day": last_day,
        "value_cap": value_cap,
    }
    row = (await db.execute(_STATEMENTS[kind], params)).one()
    result = RefreshResult(int(row.written), int(row.removed), int(row.folded))
    ATTRIBUTION_FOLDED_VALUES.inc(result.folded)
    return result


async def refresh_attribution(
    db: AsyncSession,
    project_id: uuid.UUID,
    promoted_keys: Sequence[str],
    days: Sequence[date],
    *,
    backfill_days: Sequence[date] = (),
) -> RefreshResult:
    """Recompute every kind and every promoted key over `days`, then prune the other keys.

    With `backfill_days`, a promoted key that has no row on or after the first of them (just
    promoted, or never set on a trace) is recomputed over `backfill_days` instead of `days`.
    The `metadata` rows of keys not in `promoted_keys` are deleted, whatever their day; they
    count as removed.
    """
    result = RefreshResult()
    for kind in ("trace_name", "user", "release"):
        result += await refresh_group(db, project_id, kind, None, days)
    backfilled = (
        await _keys_without_rows(db, project_id, promoted_keys, min(backfill_days))
        if backfill_days
        else set()
    )
    for key in promoted_keys:
        key_days = backfill_days if key in backfilled else days
        result += await refresh_group(db, project_id, "metadata", key, key_days)
    pruned = await db.execute(
        _PRUNE_KEYS, {"project_id": project_id, "promoted": list(promoted_keys)}
    )
    return result + RefreshResult(removed=int(getattr(pruned, "rowcount", 0)))


async def _keys_without_rows(
    db: AsyncSession, project_id: uuid.UUID, promoted_keys: Sequence[str], since: date
) -> set[str]:
    params = {"project_id": project_id, "promoted": list(promoted_keys), "since": since}
    present = (await db.execute(_KEYS_WITH_ROWS, params)).scalars().all()
    return set(promoted_keys) - {str(key) for key in present}

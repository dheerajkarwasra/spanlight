"""Bring a budget's hour rows up to date: fold the writers' deltas, recompute the stale ones.

Every UTC hour of a budget's period is a `budget_bucket_snapshots` row (migration 1032, spec
`docs/specs/2026-11-01-budget-day-snapshots.md` §8). A writer that can price its change
exactly appends a `budget_bucket_deltas` row (an INSERT, no lock): the hour's true total is
`committed_usd + sum(deltas)`. A writer that cannot bumps the hour's `budget_bucket_versions`
generation, and the row is stale until it is recomputed from the data. `compact_budget_deltas`
does both for a set of hours, in the order spec D8.4 fixes: create the missing rows at
generation -1 (never a version, so they stay stale until computed), then one statement that
locks the rows `FOR UPDATE` first, deletes the hours' deltas, recomputes the stale hours from
the three terms against each hour's version, adds the deleted deltas to the exact hours and
returns every hour's new total. One statement is one snapshot: the deltas it deletes are
exactly those whose data its scan sees, and a delta committed later is neither deleted nor
scanned, so it is folded next time. The row lock comes first so admission and the reaper's
compaction cannot both fold the same delta: the lock returns the row as the other compactor
left it (`FOR UPDATE` re-reads a row updated meanwhile), the fold adds to that value, and a
delta the other one deleted is skipped by this one's DELETE. A recompute that lost the race
stores its older total under its older generation, which is stale again at once.

The three terms (`_terms`) are the budget's spend of an hour range as `(bucket, amount)` rows:
uncorrelated stored spans by `started_at`; this budget's settled or reaped reservations by the
admission clock `expires_at - 12 minutes` (the lease, `reserve.RESERVATION_LEASE`;
`period_start` is `date_trunc` of that clock, so the hours of a period partition exactly the
rows the full scan selects by period); deleted uncorrelated spans by `started_at`. Each keeps
its row-local predicates. The spans trigger of migration 1033 evaluates the same scope rules
in plpgsql for its deltas; `tests/budgets/test_snapshot_equality.py` is the guard against the
two drifting.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.budgets.scope_parse import parse_metadata_scope
from app.budgets.types import BudgetScope
from app.core.model_match import like_prefix, model_filter_sql

HOUR = timedelta(hours=1)

_SCOPE = {
    BudgetScope.PROJECT: "TRUE",
    BudgetScope.GATEWAY_KEY: "s.source_key_id = CAST(:scope_id AS uuid)",
    BudgetScope.MODEL: model_filter_sql("s.model", "scope_id"),
    BudgetScope.USER: "t.external_user_id = :scope_id",
    BudgetScope.TRACE_NAME: "t.name = :scope_id",
    BudgetScope.METADATA: (
        "t.metadata @> jsonb_build_object(CAST(:metadata_key AS text), "
        "CAST(:metadata_value AS text))"
    ),
}
_TRACE_SCOPES = {BudgetScope.USER, BudgetScope.TRACE_NAME, BudgetScope.METADATA}
_DELETED_SCOPE = {
    BudgetScope.PROJECT: "TRUE",
    BudgetScope.GATEWAY_KEY: "d.source_key_id = CAST(:scope_id AS uuid)",
    BudgetScope.MODEL: model_filter_sql("d.model", "scope_id"),
    BudgetScope.USER: "TRUE",
    BudgetScope.TRACE_NAME: "TRUE",
    BudgetScope.METADATA: "TRUE",
}
# The reservation term's range: the period column (the full scan) or the admission clock inside
# `[:lo, :hi)`, written on `expires_at` so `budget_reservations_budget_expires_idx` serves it.
BY_PERIOD = "r.period_start = :lo"
BY_CLOCK = (
    "r.expires_at >= :lo + interval '12 minutes' AND r.expires_at < :hi + interval '12 minutes'"
)


def terms(scope: BudgetScope, reservations: str) -> str:
    """The three disjoint terms as `(bucket, amount)` rows over `[:lo, :hi)`: uncorrelated
    stored spans, this budget's settled or reaped calls, and deleted uncorrelated spans."""
    join = (
        "JOIN traces t ON t.project_id = s.project_id AND t.trace_id = s.trace_id"
        if scope in _TRACE_SCOPES
        else ""
    )
    return f"""
        SELECT date_trunc('hour', s.started_at, 'UTC') AS bucket, s.cost_usd AS amount
        FROM spans s {join}
        WHERE s.project_id = :project_id
          AND s.started_at >= :lo AND s.started_at < :hi
          AND NOT s.usage_rollup AND {_SCOPE[scope]}
          AND NOT EXISTS (
              SELECT 1 FROM budget_reservations r
              WHERE r.project_id = s.project_id AND r.budget_id = :budget_id
                AND r.trace_id = s.trace_id AND r.span_id = s.span_id)
        UNION ALL
        SELECT date_trunc('hour', r.expires_at - interval '12 minutes', 'UTC'),
               CASE WHEN s.usage_rollup THEN 0
                    ELSE coalesce(s.cost_usd, r.charged_usd, r.amount_usd) END
        FROM budget_reservations r
        LEFT JOIN spans s ON s.project_id = r.project_id
                         AND s.trace_id = r.trace_id AND s.span_id = r.span_id
        {join.replace("JOIN", "LEFT JOIN", 1)}
        WHERE r.project_id = :project_id AND r.budget_id = :budget_id
          AND (NOT r.reclassified OR s.span_id IS NULL OR {_SCOPE[scope]})
          AND {reservations} AND r.status IN ('settled', 'reaped')
        UNION ALL
        SELECT date_trunc('hour', d.started_at, 'UTC'), d.cost_usd
        FROM budget_deleted_spans d
        WHERE d.project_id = :project_id
          AND d.started_at >= :lo AND d.started_at < :hi
          AND {_DELETED_SCOPE[scope]}
          AND NOT EXISTS (
              SELECT 1 FROM budget_reservations r
              WHERE r.project_id = d.project_id AND r.budget_id = :budget_id
                AND r.trace_id = d.trace_id AND r.span_id = d.span_id)
          AND NOT EXISTS (
              SELECT 1 FROM spans s2
              WHERE s2.project_id = d.project_id
                AND s2.trace_id = d.trace_id AND s2.span_id = d.span_id)
    """


def scope_params(
    project_id: uuid.UUID, budget_id: uuid.UUID, scope: BudgetScope, scope_id: str | None
) -> dict[str, object]:
    params: dict[str, object] = {
        "project_id": project_id,
        "budget_id": budget_id,
        "scope_id": scope_id,
        "scope_id_like": like_prefix(scope_id or ""),
    }
    if scope is BudgetScope.METADATA:
        params["metadata_key"], params["metadata_value"] = parse_metadata_scope(scope_id or "")
    return params


_PLACEHOLDERS = text("""
    INSERT INTO budget_bucket_snapshots
           (project_id, budget_id, bucket_start, committed_usd, generation)
    SELECT :project_id, :budget_id, h.hour, 0, -1
    FROM unnest(CAST(:hours AS timestamptz[])) AS h(hour)
    ON CONFLICT DO NOTHING
""")

_NO_TOTALS = "SELECT NULL::timestamptz AS bucket, NULL::numeric AS total WHERE FALSE"


def _fold_statement(scope: BudgetScope | None) -> str:
    """Delete the hours' deltas, recompute the stale hours in one pass over `[:lo, :hi)`
    against the version each is read with here, add the deltas to the rest, store and return
    every hour's total. The stale hours' deltas are deleted and not added: the scan sees their
    data."""
    totals = (
        f"""SELECT bucket, sum(amount) AS total FROM ({terms(scope, BY_CLOCK)}) x
            WHERE bucket = ANY(CAST(:stale AS timestamptz[]))
            GROUP BY bucket"""
        if scope is not None
        else _NO_TOTALS
    )
    return f"""
        WITH locked AS (
            SELECT bucket_start, committed_usd, generation FROM budget_bucket_snapshots
            WHERE project_id = :project_id AND budget_id = :budget_id
              AND bucket_start = ANY(CAST(:hours AS timestamptz[]))
            ORDER BY bucket_start FOR UPDATE),
        gone AS (
            DELETE FROM budget_bucket_deltas d
            WHERE d.project_id = :project_id AND d.budget_id = :budget_id
              AND d.bucket_start IN (SELECT bucket_start FROM locked)
            RETURNING d.bucket_start, d.amount_usd),
        folded AS (
            SELECT bucket_start, sum(amount_usd) AS total FROM gone GROUP BY bucket_start),
        totals AS ({totals}),
        fresh AS (
            SELECT l.bucket_start AS hour,
                   CASE WHEN l.bucket_start = ANY(CAST(:stale AS timestamptz[]))
                        THEN coalesce(t.total, 0)
                        ELSE l.committed_usd + coalesce(f.total, 0) END AS committed_usd,
                   CASE WHEN l.bucket_start = ANY(CAST(:stale AS timestamptz[]))
                        THEN coalesce(v.generation, 0)
                        ELSE l.generation END AS generation
            FROM locked l
            LEFT JOIN totals t ON t.bucket = l.bucket_start
            LEFT JOIN folded f ON f.bucket_start = l.bucket_start
            LEFT JOIN budget_bucket_versions v ON v.project_id = :project_id
                                              AND v.bucket_start = l.bucket_start)
        UPDATE budget_bucket_snapshots s
        SET committed_usd = fresh.committed_usd, generation = fresh.generation,
            computed_at = now()
        FROM fresh
        WHERE s.project_id = :project_id AND s.budget_id = :budget_id
          AND s.bucket_start = fresh.hour
        RETURNING s.bucket_start, s.committed_usd, (SELECT count(*) FROM gone) AS folded
    """


async def compact_budget_deltas(
    db: AsyncSession,
    project_id: uuid.UUID,
    budget_id: uuid.UUID,
    hours: Sequence[datetime],
    *,
    missing: Sequence[datetime] = (),
    stale: Sequence[datetime] = (),
    scope: BudgetScope | None = None,
    scope_id: str | None = None,
) -> tuple[dict[datetime, Decimal], int]:
    """Fold the deltas of `hours` into their rows, creating the `missing` ones first and
    recomputing the `stale` ones (subsets; `stale` needs `scope`). Returns each hour's new
    `committed_usd` and how many deltas were folded. Runs in the caller's transaction under
    the project binding or the worker bypass."""
    hours = sorted(set(hours))
    stale = sorted(set(stale))
    if not hours:
        return {}, 0
    keys = {"project_id": project_id, "budget_id": budget_id}
    if missing:
        await db.execute(_PLACEHOLDERS, {**keys, "hours": sorted(set(missing))})
    params: dict[str, object] = {**keys, "hours": hours, "stale": stale}
    if stale:
        if scope is None:
            raise ValueError("recomputing stale hours needs the budget's scope")
        params |= scope_params(project_id, budget_id, scope, scope_id)
        params |= {"lo": stale[0], "hi": stale[-1] + HOUR}
    rows = (await db.execute(text(_fold_statement(scope if stale else None)), params)).all()
    totals = {row.bucket_start: Decimal(row.committed_usd) for row in rows}
    return totals, int(rows[0].folded) if rows else 0

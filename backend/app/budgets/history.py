"""Fresh, disjoint budget accounting under the caller's locked period counter.

Stored spans without this budget's correlation form history. Correlation excludes an identity
across periods: a corrected timestamp must not charge an admitted call in a second period.
Correlated calls contribute their stored cost (or retained conservative charge) exactly once,
even after telemetry deletion. Deleted uncorrelated priced spans of the month are counted from
`budget_deleted_spans` (migration 1031), by key and model for those scopes and in full for the
trace-bound scopes (the trace is gone, so conservatively), unless the span was stored again.
The missing-history marker is set only by that upgrade. Evaluator caches and rollups are not
part of admission.

`read_history` sums every UTC hour of the period from `budget_bucket_snapshots` (migrations
1032 and 1033): one statement lists the hours with their row, version and pending deltas and
folds the deltas of the fresh hours (at 50 calls and 50 batches a second that is the open hour
with a delta or two). Only stale or missing hours go through `app.budgets.compaction` (one more
statement, two when an hour has no row yet, at most once per writer change). The common case
costs one statement. `scan_history` is the old full-range statement
over the same term SQL: the oracle of the equality test
(`tests/budgets/test_snapshot_equality.py`) and the repair path (deleting snapshot rows).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.budgets.compaction import (
    BY_PERIOD,
    HOUR,
    compact_budget_deltas,
    scope_params,
    terms,
)
from app.budgets.types import BudgetScope

# The reservation lease: `expires_at - 12 minutes` is the immutable admission clock, the
# literal in the term SQL and the trigger functions.
LEASE = timedelta(minutes=12)


@dataclass(frozen=True)
class HistoryTotal:
    committed_usd: Decimal
    legacy_usd: Decimal
    missing_through: datetime | None
    retention_days: int


# The hours of `[:started, :last]` with their row, version and pending deltas, plus the counter
# and project columns: the exact rows' sum and the hours a compaction must touch. It never
# locks the counter: under READ COMMITTED a `FOR UPDATE` here would re-read the counter row
# after the lock wait while the hours kept the statement's older snapshot, so a settle
# committing during the wait would be half-seen. The caller locks the counter in a statement of
# its own first.
#
# The common case, a current hour whose row is fresh but has deltas, is folded here in the same
# statement as `app.budgets.compaction` folds it (lock the rows, delete their deltas, add them):
# one round trip instead of two. `folded_usd` is those hours' new totals; `touched` lists only
# the stale or missing hours, which still need the compaction's recompute.
_HOURS = text("""
    WITH hours AS (
        SELECT h.hour, s.committed_usd,
               s.generation IS DISTINCT FROM coalesce(v.generation, 0) AS stale,
               EXISTS (SELECT 1 FROM budget_bucket_deltas d
                       WHERE d.project_id = :project_id AND d.budget_id = :budget_id
                         AND d.bucket_start = h.hour) AS pending
        FROM generate_series(CAST(:started AS timestamptz), CAST(:last AS timestamptz),
                             interval '1 hour') AS h(hour)
        LEFT JOIN budget_bucket_snapshots s ON s.project_id = :project_id
                                           AND s.budget_id = :budget_id
                                           AND s.bucket_start = h.hour
        LEFT JOIN budget_bucket_versions v ON v.project_id = :project_id
                                          AND v.bucket_start = h.hour),
    locked AS (
        SELECT s.bucket_start, s.committed_usd FROM budget_bucket_snapshots s
        WHERE s.project_id = :project_id AND s.budget_id = :budget_id
          AND s.bucket_start IN (SELECT hour FROM hours WHERE pending AND NOT stale)
        ORDER BY s.bucket_start FOR UPDATE),
    gone AS (
        DELETE FROM budget_bucket_deltas d
        WHERE d.project_id = :project_id AND d.budget_id = :budget_id
          AND d.bucket_start IN (SELECT bucket_start FROM locked)
        RETURNING d.bucket_start, d.amount_usd),
    folded AS (
        SELECT bucket_start, sum(amount_usd) AS total FROM gone GROUP BY bucket_start),
    stored AS (
        UPDATE budget_bucket_snapshots s
        SET committed_usd = l.committed_usd + coalesce(f.total, 0), computed_at = now()
        FROM locked l LEFT JOIN folded f ON f.bucket_start = l.bucket_start
        WHERE s.project_id = :project_id AND s.budget_id = :budget_id
          AND s.bucket_start = l.bucket_start
        RETURNING s.committed_usd)
    SELECT p.budget_history_missing_through, p.retention_days,
           c.legacy_unattributed_usd, c.reserved_usd,
           (SELECT coalesce(sum(committed_usd) FILTER (WHERE NOT stale AND NOT pending), 0)
            FROM hours) AS valid_usd,
           (SELECT coalesce(sum(committed_usd), 0) FROM stored) AS folded_usd,
           (SELECT array_agg(hour ORDER BY hour) FILTER (WHERE stale) FROM hours) AS touched,
           (SELECT array_agg(hour ORDER BY hour) FILTER (WHERE stale) FROM hours) AS stale,
           (SELECT array_agg(hour ORDER BY hour) FILTER (WHERE committed_usd IS NULL)
            FROM hours) AS missing
    FROM projects p JOIN budget_counters c ON c.project_id = p.id
    WHERE p.id = :project_id AND c.budget_id = :budget_id AND c.period_start = :started
""")


def _total(row: Row[Any], committed_usd: Decimal) -> HistoryTotal:
    return HistoryTotal(
        committed_usd,
        Decimal(row.legacy_unattributed_usd),
        row.budget_history_missing_through,
        row.retention_days,
    )


async def read_history(
    db: AsyncSession,
    project_id: uuid.UUID,
    budget_id: uuid.UUID,
    scope: BudgetScope,
    scope_id: str | None,
    started: datetime,
    ended: datetime,
    *,
    now: datetime,
) -> HistoryTotal:
    """The period's hours from their rows, the touched ones compacted or recomputed first.

    Hours through the one after `now` (ingest accepts ten minutes of clock skew). Runs after
    the counter lock, in a statement of its own and so in a snapshot taken after the lock was
    granted, so tracked settlement and reaping cannot skew it; untracked ingestion
    that commits meanwhile is read on the next admission, as before. The statements are
    separate READ COMMITTED snapshots: an untracked write committing between them is in some
    hours' values and not others, which is the accepted skew (spec D6), since every hour's
    value is exact as of a moment after the counter lock and the next admission sees all.
    """
    last = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0) + HOUR
    last = min(max(last, started), ended - HOUR)
    row = (
        await db.execute(
            _HOURS,
            {"project_id": project_id, "budget_id": budget_id, "started": started, "last": last},
        )
    ).one()
    committed = Decimal(row.valid_usd) + Decimal(row.folded_usd)
    if row.touched:
        totals, _ = await compact_budget_deltas(
            db,
            project_id,
            budget_id,
            row.touched,
            missing=row.missing or (),
            stale=row.stale or (),
            scope=scope,
            scope_id=scope_id,
        )
        committed += sum(totals.values(), Decimal(0))
    return _total(row, committed + Decimal(row.legacy_unattributed_usd) + Decimal(row.reserved_usd))


async def scan_history(
    db: AsyncSession,
    project_id: uuid.UUID,
    budget_id: uuid.UUID,
    scope: BudgetScope,
    scope_id: str | None,
    started: datetime,
    ended: datetime,
) -> HistoryTotal:
    """The whole period in one statement and one READ COMMITTED snapshot: the oracle."""
    row = (
        await db.execute(
            text(f"""
                SELECT p.budget_history_missing_through, p.retention_days,
                       c.legacy_unattributed_usd,
                       c.legacy_unattributed_usd + c.reserved_usd
                       + (SELECT coalesce(sum(amount), 0) FROM ({terms(scope, BY_PERIOD)}) x)
                         AS committed_usd
                FROM projects p JOIN budget_counters c ON c.project_id = p.id
                WHERE p.id = :project_id AND c.budget_id = :budget_id
                  AND c.period_start = :started
            """),
            {
                **scope_params(project_id, budget_id, scope, scope_id),
                "started": started,
                "lo": started,
                "hi": ended,
            },
        )
    ).one()
    return _total(row, Decimal(row.committed_usd))

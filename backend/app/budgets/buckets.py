"""Bump the budget bucket versions a trace write or delete will touch, in one sorted statement.

The statement triggers of migration 1032 bump a version row per hour they touch, one statement
at a time: an ingest transaction fires the `traces` trigger and then the `spans` trigger, and a
trace delete cascades one `spans` DELETE per trace, so their rows would be taken in an order
that can cycle with another transaction's. Every such writer calls `bump_hours_of_traces`
first: one statement over every hour the traces' spans, trace starts, reservations (admission
clock) and ledger rows fall in, plus the hours the writer is about to add, inserted in
`(project_id, bucket_start)` order; the triggers then only find rows the transaction already
holds. Projects without a budget are skipped, as the triggers skip them. The statement also
returns the traces' current span and trace-start hours, which ingestion marks dirty afterwards,
so it replaces that read rather than adding a round trip.

A write bumps nothing unless one of its spans is tracked (migration 1033): an identity with a
settled or reaped reservation or a ledger row. Untracked writes are priced by the spans
trigger as deltas, and the only version rows their transaction takes are the `traces`
trigger's, in one sorted statement; so an ordinary batch leaves the open hour exact. A delete
always bumps.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_BUMP = text("""
    WITH touched AS (SELECT CAST(:project_id AS uuid) AS project_id,
                            unnest(CAST(:trace_ids AS text[])) AS trace_id),
         current AS (
             SELECT date_trunc('hour', x.started_at, 'UTC') AS hour
             FROM traces x JOIN touched t
               ON t.project_id = x.project_id AND t.trace_id = x.trace_id
             UNION
             SELECT date_trunc('hour', s.started_at, 'UTC')
             FROM spans s JOIN touched t
               ON t.project_id = s.project_id AND t.trace_id = s.trace_id),
         hours AS (
             SELECT hour FROM current
             UNION
             SELECT unnest(CAST(:extra_hours AS timestamptz[]))
             UNION
             SELECT date_trunc('hour', r.expires_at - interval '12 minutes', 'UTC')
             FROM budget_reservations r JOIN touched t
               ON t.project_id = r.project_id AND t.trace_id = r.trace_id
             UNION
             SELECT date_trunc('hour', d.started_at, 'UTC')
             FROM budget_deleted_spans d JOIN touched t
               ON t.project_id = d.project_id AND t.trace_id = d.trace_id),
         bumped AS (
             INSERT INTO budget_bucket_versions (project_id, bucket_start, generation)
             SELECT CAST(:project_id AS uuid), h.hour, nextval('budget_bucket_generation_seq')
             FROM hours h
             WHERE EXISTS (SELECT 1 FROM budgets b WHERE b.project_id = CAST(:project_id AS uuid))
               AND (NOT :gated OR EXISTS (
                   SELECT 1
                   FROM unnest(CAST(:span_trace_ids AS text[]), CAST(:span_ids AS text[]))
                        AS i(trace_id, span_id)
                   WHERE EXISTS (SELECT 1 FROM budget_reservations r
                                 WHERE r.project_id = CAST(:project_id AS uuid)
                                   AND r.trace_id = i.trace_id AND r.span_id = i.span_id
                                   AND r.status IN ('settled', 'reaped'))
                      OR EXISTS (SELECT 1 FROM budget_deleted_spans d
                                 WHERE d.project_id = CAST(:project_id AS uuid)
                                   AND d.trace_id = i.trace_id AND d.span_id = i.span_id)))
             ORDER BY h.hour
             ON CONFLICT (project_id, bucket_start)
             DO UPDATE SET generation = nextval('budget_bucket_generation_seq')
             RETURNING bucket_start)
    SELECT hour FROM current ORDER BY hour
""")


async def bump_hours_of_traces(
    db: AsyncSession,
    project_id: uuid.UUID,
    trace_ids: Sequence[str],
    extra_hours: Sequence[datetime] = (),
    *,
    writing: Sequence[tuple[str, str]] | None = None,
) -> list[datetime]:
    """Take, sorted, every version row that writing `extra_hours` to or deleting `trace_ids`
    will bump, and return the traces' current trace-start and span hours (as
    `app.ingest.aggregate_changes.trace_hours` does). `writing` is the batch's span identities
    `(trace_id, span_id)`: given, the rows are taken only if one of them is tracked, as the
    spans trigger bumps only for those; None is a delete, which always bumps."""
    if not trace_ids:
        return []
    rows = await db.scalars(
        _BUMP,
        {
            "project_id": project_id,
            "trace_ids": list(trace_ids),
            "extra_hours": sorted(set(extra_hours)),
            "gated": writing is not None,
            "span_trace_ids": [trace_id for trace_id, _ in writing or ()],
            "span_ids": [span_id for _, span_id in writing or ()],
        },
    )
    return list(rows)

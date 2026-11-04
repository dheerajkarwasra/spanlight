"""Bounded, tenant-independent discovery; repair rechecks coverage under the source lock."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import bypass_rls

# Disabled daily obligations are runnable only when durable evidence permits settlement.
_RUNNABLE = """
    h.pending_consumers = 0 OR (h.pending_consumers & :enabled) <> 0
    OR ((h.pending_consumers & 14) <> 0 AND (
        p.aggregate_history_unverified_before IS NULL
        OR date_trunc('day', h.bucket_hour, 'UTC') < p.aggregate_history_unverified_before
        OR EXISTS (
            SELECT 1 FROM aggregate_incomplete_hours i
            WHERE i.project_id = h.project_id
              AND i.bucket_hour >= date_trunc('day', h.bucket_hour, 'UTC')
              AND i.bucket_hour < date_trunc('day', h.bucket_hour, 'UTC') + interval '1 day'
        )
    ))
"""
_PAIRS = f"""
    SELECT h.project_id, h.bucket_hour, min(h.first_marked_at) AS first_marked_at,
           bool_or({_RUNNABLE}) AS runnable
    FROM (
        SELECT project_id, bucket_hour, first_marked_at, pending_consumers, -1 AS shard
        FROM aggregate_dirty_hours
        UNION ALL
        SELECT project_id, bucket_hour, first_marked_at, pending_consumers, shard
        FROM aggregate_dirty_shards
    ) h JOIN projects p ON p.id = h.project_id
    GROUP BY h.project_id, h.bucket_hour
"""
# Walk rows of both tables oldest first, as the one-table scan did: a pair is offered at the age
# of its oldest runnable row, and repair then settles every row of that hour. Ordering is only
# priority; the attempted list keeps a pair from being offered twice in one pass.
_CANDIDATE = text(f"""
    SELECT h.project_id, h.bucket_hour, h.first_marked_at
    FROM (
        SELECT project_id, bucket_hour, first_marked_at, pending_consumers
        FROM aggregate_dirty_hours
        UNION ALL
        SELECT project_id, bucket_hour, first_marked_at, pending_consumers
        FROM aggregate_dirty_shards
    ) h JOIN projects p ON p.id = h.project_id
    WHERE ({_RUNNABLE})
      AND NOT (h.project_id = ANY(CAST(:excluded AS uuid[])))
      AND NOT EXISTS (
          SELECT 1 FROM unnest(CAST(:attempt_projects AS uuid[]),
                              CAST(:attempt_hours AS timestamptz[])) a(project_id, hour)
          WHERE a.project_id = h.project_id AND a.hour = h.bucket_hour
      )
      AND (CAST(:cursor_age AS timestamptz) IS NULL OR
           (h.first_marked_at, h.project_id, h.bucket_hour) >
           (CAST(:cursor_age AS timestamptz), CAST(:cursor_project AS uuid),
            CAST(:cursor_hour AS timestamptz)))
    ORDER BY h.first_marked_at, h.project_id, h.bucket_hour
    LIMIT 1
""")
_BACKLOG = text(f"""
    WITH pairs AS ({_PAIRS})
    SELECT count(*) AS depth,
           coalesce(extract(epoch FROM clock_timestamp() - min(first_marked_at)), 0) AS age,
           count(*) FILTER (WHERE runnable) AS runnable
    FROM pairs
""")


@dataclass(frozen=True)
class Candidate:
    project_id: uuid.UUID
    hour: datetime
    age: datetime


@dataclass
class Scan:
    attempted: list[Candidate] = field(default_factory=list)
    round_projects: set[uuid.UUID] = field(default_factory=set)
    failed_projects: set[uuid.UUID] = field(default_factory=set)
    cursor: Candidate | None = None

    async def next(self, db: AsyncSession, enabled: int) -> Candidate | None:
        await bypass_rls(db)
        params = {
            "enabled": enabled,
            "excluded": list(self.round_projects | self.failed_projects),
            "attempt_projects": [c.project_id for c in self.attempted],
            "attempt_hours": [c.hour for c in self.attempted],
            "cursor_age": self.cursor.age if self.cursor else None,
            "cursor_project": self.cursor.project_id if self.cursor else None,
            "cursor_hour": self.cursor.hour if self.cursor else None,
        }
        row = (await db.execute(_CANDIDATE, params)).first()
        if row is None:
            return None
        candidate = Candidate(row.project_id, row.bucket_hour, row.first_marked_at)
        self.cursor = candidate
        self.round_projects.add(candidate.project_id)
        self.attempted.append(candidate)
        return candidate

    def next_round(self) -> None:
        self.round_projects.clear()
        self.cursor = None


async def backlog(db: AsyncSession, enabled: int) -> tuple[int, float, int]:
    await bypass_rls(db)
    row = (await db.execute(_BACKLOG, {"enabled": enabled})).one()
    return int(row.depth), max(0.0, float(row.age)), int(row.runnable)

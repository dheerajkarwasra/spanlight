"""Read coverage and values in one PostgreSQL snapshot, including empty results."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import DateTime, Integer, Row, TextClause, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import BindParameter
from sqlalchemy.sql.visitors import iterate

from app.core.observability import AGGREGATE_UNAVAILABLE_READS
from app.rollups.dirty import ALL


@dataclass
class CoverageState:
    """Unavailable dominates across statements contributing to one response."""

    unavailable: bool = False
    oldest_pending_at: datetime | None = None

    @property
    def status(self) -> Literal["complete", "pending", "unavailable"]:
        if self.unavailable:
            return "unavailable"
        return "pending" if self.oldest_pending_at is not None else "complete"

    def metadata(self) -> dict[str, Any]:
        return {"aggregate_status": self.status, "oldest_pending_at": self.oldest_pending_at}


async def read_covered(
    db: AsyncSession,
    statement: TextClause,
    params: dict[str, Any],
    project_id: uuid.UUID,
    start: datetime,
    end: datetime,
    state: CoverageState | None = None,
    *,
    relevant_consumers: int,
    raw: bool = False,
) -> list[Row[Any]]:
    """Raw values require complete coverage; materialized values remain usable snapshots.

    The explicit presence column distinguishes the outer-join sentinel from valid NULL dimensions.
    Preserve the original statement's typed parameters when composing the coverage CTE.
    """
    return await read_covered_groups(
        db,
        statement,
        params,
        project_id,
        [(0, start, end, state)],
        relevant_consumers=relevant_consumers,
        raw=raw,
    )


async def read_covered_groups(
    db: AsyncSession,
    statement: TextClause,
    params: dict[str, Any],
    project_id: uuid.UUID,
    groups: Sequence[tuple[int, datetime, datetime, CoverageState | None]],
    *,
    relevant_consumers: int,
    raw: bool = False,
    grouped: bool = False,
) -> list[Row[Any]]:
    """:func:`read_covered` with coverage judged per group: each ``(group, start, end, state)``
    gets its own completeness and pending age, recorded in its own state.

    With ``grouped`` the statement's rows carry a ``grp`` column, and a raw read keeps only the
    rows of complete groups; otherwise the rows are returned once and gated by every group.
    """
    if type(relevant_consumers) is not int or not 0 <= relevant_consumers <= ALL:
        raise ValueError("relevant_consumers must be an integer mask from 0 through ALL")
    complete = "c.complete FROM aggregate_coverage c WHERE c.grp = source.grp"
    every = "bool_and(complete) FROM aggregate_coverage"
    gate = f"WHERE (SELECT {complete if grouped else every})" if raw else ""
    join = "v.grp = c.grp" if grouped else "c.grp = (SELECT min(grp) FROM bounds)"
    # One group needs no group column, so read_covered rows keep their old shape for callers
    # that unpack them by name (reconciliation builds a dataclass from every column).
    many = len(groups) > 1
    wrapped = text(f"""
        WITH recent_progress AS (
            SELECT admitted_hour, next_closed_hour, closed_pending_since
            FROM aggregate_job_progress
            WHERE project_id = :coverage_project AND kind = 'rollup_recent'
        ), bounds AS (
            SELECT * FROM unnest(
                CAST(:coverage_groups AS integer[]),
                CAST(:coverage_starts AS timestamptz[]),
                CAST(:coverage_ends AS timestamptz[])
            ) AS b(grp, start_at, end_at)
        ), aggregate_coverage AS (
            SELECT b.grp,
                   coalesce((SELECT b.start_at >= p.aggregate_history_unverified_before
                            FROM projects p WHERE p.id = :coverage_project), false)
                   AND NOT EXISTS (
                       SELECT 1 FROM aggregate_incomplete_hours h
                       WHERE h.project_id = :coverage_project
                         AND h.bucket_hour >= b.start_at AND h.bucket_hour < b.end_at
                   ) AS complete,
                   least((SELECT min(h.first_marked_at) FROM (
                        SELECT project_id, bucket_hour, first_marked_at, pending_consumers
                        FROM aggregate_dirty_hours
                        UNION ALL
                        SELECT project_id, bucket_hour, first_marked_at, pending_consumers
                        FROM aggregate_dirty_shards
                    ) h
                    WHERE h.project_id = :coverage_project
                      AND h.bucket_hour >= b.start_at AND h.bucket_hour < b.end_at
                      AND (h.pending_consumers & CAST(:relevant_consumers AS integer)) <> 0
                   ), (SELECT min(coalesce(closed_pending_since,
                                           next_closed_hour + interval '1 hour 10 minutes'))
                       FROM recent_progress
                       WHERE (CAST(:relevant_consumers AS integer) & 1) <> 0
                         AND greatest(b.start_at, admitted_hour, next_closed_hour)
                             < least(b.end_at, date_trunc('hour',
                                     statement_timestamp() - interval '10 minutes', 'UTC'))
                   )) AS oldest_pending_at,
                   ((CAST(:relevant_consumers AS integer) & 1) = 0
                     OR EXISTS (SELECT 1 FROM recent_progress)) AS progress_present
            FROM bounds b
        ), aggregate_values AS (
            SELECT source.*, true AS aggregate_present FROM ({statement.text}) source {gate}
        )
        SELECT {"c.grp AS aggregate_grp, " if many else ""}c.progress_present
                   AS aggregate_progress_present,
               c.complete AS aggregate_complete, c.oldest_pending_at AS aggregate_pending_at, v.*
        FROM aggregate_coverage c LEFT JOIN aggregate_values v ON {join}
    """).bindparams(
        *(node for node in iterate(statement) if isinstance(node, BindParameter)),
        bindparam("coverage_groups", type_=ARRAY(Integer())),
        bindparam("coverage_starts", type_=ARRAY(DateTime(timezone=True))),
        bindparam("coverage_ends", type_=ARRAY(DateTime(timezone=True))),
    )
    rows = (
        await db.execute(
            wrapped,
            {
                **params,
                "coverage_project": project_id,
                "coverage_groups": [group for group, *_ in groups],
                "coverage_starts": [start for _, start, _, _ in groups],
                "coverage_ends": [end for _, _, end, _ in groups],
                "relevant_consumers": relevant_consumers,
            },
        )
    ).all()
    if not rows[0].aggregate_progress_present:
        raise RuntimeError("Required recent aggregate admission is missing")
    first = {row.aggregate_grp if many else groups[0][0]: row for row in reversed(rows)}
    for group, _, _, state in groups:
        row = first[group]
        if not row.aggregate_complete:
            AGGREGATE_UNAVAILABLE_READS.inc()
            if state is not None:
                state.unavailable = True
        pending = row.aggregate_pending_at
        if (
            state is not None
            and pending is not None
            and (state.oldest_pending_at is None or pending < state.oldest_pending_at)
        ):
            state.oldest_pending_at = pending
    return [row for row in rows if row.aggregate_present is True]

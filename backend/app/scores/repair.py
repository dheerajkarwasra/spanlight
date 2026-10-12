"""Heal `traces.has_scores` flags a race left unset. Worker code only: it bypasses row-level
security, because the sweep spans projects.

A score write and the ingest of its trace can overlap: the score's `UPDATE traces` finds no row
(the trace is not committed yet) while the trace's insert checks for scores before the score is
committed, so both commit and the flag stays false. A later span batch for the trace heals it
(the trace upsert ORs the flag in); this sweep covers the trace that gets no later batch. It looks
only at scores created in the last `REPAIR_WINDOW`, read through the BRIN index on
`scores.created_at`, and fixes at most `REPAIR_LIMIT` traces, so each run stays small; the window
is several times the period of the job that runs it (`rollup_hourly`, every 5 minutes).

Trace rows are locked in `(project_id, trace_id)` order, the order ingestion locks them in
within a project, so the sweep and an ingest cannot deadlock on each other.
"""

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import bypass_rls

REPAIR_WINDOW = timedelta(minutes=15)
# Traces fixed per run at most; the next run picks up the rest (the window covers three runs).
REPAIR_LIMIT = 5000

_REPAIR = text(
    """
    WITH due AS (
        SELECT t.project_id, t.trace_id
        FROM traces AS t
        JOIN (
            SELECT DISTINCT project_id, trace_id FROM scores WHERE created_at >= :since
        ) AS s ON s.project_id = t.project_id AND s.trace_id = t.trace_id
        WHERE NOT t.has_scores
        ORDER BY t.project_id, t.trace_id
        LIMIT :limit
        FOR UPDATE OF t
    )
    UPDATE traces AS t SET has_scores = true
    FROM due
    WHERE t.project_id = due.project_id AND t.trace_id = due.trace_id
    """
)


async def repair_has_scores(db: AsyncSession, now: datetime) -> int:
    """Set `has_scores` on traces with a recent score whose flag is false. Returns how many.

    Bypasses row-level security for the session's transaction; the caller commits.
    """
    await bypass_rls(db)
    result = await db.execute(_REPAIR, {"since": now - REPAIR_WINDOW, "limit": REPAIR_LIMIT})
    return int(getattr(result, "rowcount", 0) or 0)

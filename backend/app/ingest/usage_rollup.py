"""Usage rollups: flag the spans whose usage repeats a descendant's (Bet 4 spec §5.3).

A span carries usage when either token count is set. It is a usage rollup when any span
reachable from it through `parent_span_id`, within the same `(project_id, trace_id)`, carries
usage too: an agent framework's outer span over the provider call it wraps, or an SDK span a
caller joined to the gateway's span by hand. The flag is a pure function of the stored rows, so a
replay changes nothing and the flags converge whatever order the spans arrive in; every batch
recomputes its whole traces, under the trace row locks the span upsert took.

The recursion climbs from each usage span to its ancestors and stops at depth 64, which bounds a
malformed cycle (`a -> b -> a`: both are flagged, the honest reading of a broken parent chain)
without the `CYCLE` clause. Only rows whose flag changes are written. The update ignores the
gateway-ownership guard of the span upsert: the flag is derived server data, not client data.
"""

import uuid
from collections.abc import Sequence

from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncSession

USAGE_ROLLUP_RECOMPUTE: TextClause = text(
    """
    WITH RECURSIVE members AS (
        SELECT span_id, parent_span_id, trace_id,
               (input_tokens IS NOT NULL OR output_tokens IS NOT NULL) AS has_usage
        FROM spans WHERE project_id = :project_id AND trace_id = ANY(:trace_ids)
    ),
    ancestors AS (                       -- (usage span, each of its ancestors)
        SELECT m.trace_id, m.span_id AS leaf, m.parent_span_id AS ancestor, 1 AS depth
        FROM members m WHERE m.has_usage AND m.parent_span_id IS NOT NULL
        UNION ALL
        SELECT a.trace_id, a.leaf, p.parent_span_id, a.depth + 1
        FROM ancestors a JOIN members p ON p.trace_id = a.trace_id AND p.span_id = a.ancestor
        WHERE p.parent_span_id IS NOT NULL AND a.depth < 64
    ),
    rollups AS (
        SELECT DISTINCT m.trace_id, m.span_id
        FROM members m JOIN ancestors a ON a.trace_id = m.trace_id AND a.ancestor = m.span_id
        WHERE m.has_usage
    )
    UPDATE spans s
    SET usage_rollup = (r.span_id IS NOT NULL)
    FROM members m LEFT JOIN rollups r ON r.trace_id = m.trace_id AND r.span_id = m.span_id
    WHERE s.project_id = :project_id AND s.trace_id = m.trace_id AND s.span_id = m.span_id
      AND s.usage_rollup IS DISTINCT FROM (r.span_id IS NOT NULL)
    """
)


async def recompute_usage_rollups(
    session: AsyncSession, project_id: uuid.UUID, trace_ids: Sequence[str]
) -> None:
    """Set `usage_rollup` on every span of the given traces from the rows stored now."""
    await session.execute(
        USAGE_ROLLUP_RECOMPUTE, {"project_id": project_id, "trace_ids": list(trace_ids)}
    )

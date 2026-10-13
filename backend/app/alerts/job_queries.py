"""The `evaluate_alerts` job's list of projects, read across every project.

It holds nothing but ids, so it runs with row-level security bypassed, in a transaction it ends
itself (the bypass is local to it). Only worker code imports this module.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import bypass_rls

# Projects with at least one enabled rule. A project whose rules have never been evaluated, or
# were evaluated longest ago, comes first: if a pass runs out of time, the next one starts where
# this one could not reach.
_PROJECTS_WITH_ENABLED_RULES = text(
    """
    SELECT r.project_id
    FROM alert_rules AS r
    LEFT JOIN alert_states AS s ON s.rule_id = r.id
    WHERE r.enabled
    GROUP BY r.project_id
    ORDER BY bool_or(s.last_evaluated_at IS NULL) DESC, min(s.last_evaluated_at), r.project_id
    """
)


async def projects_with_enabled_rules(db: AsyncSession) -> list[uuid.UUID]:
    """Every project with an enabled rule. Ends its own transaction (the bypass is local to it)."""
    await bypass_rls(db)
    rows = (await db.execute(_PROJECTS_WITH_ENABLED_RULES)).scalars().all()
    await db.commit()
    return [uuid.UUID(str(project_id)) for project_id in rows]

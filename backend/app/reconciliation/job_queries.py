"""Reads of the reconciliation jobs that cross tenants. Only worker code imports this module.

The cost rollup job's list of projects holds nothing but ids, so it runs with row-level security
bypassed, in a transaction it ends itself (the bypass is local to it).

The billing fetch jobs read billing sources by id or across organizations, and close dead runs
across organizations. Those tables are not under row-level security, so they need no bypass;
they are the one place a source or a run is touched without its organization's id, which is why
the queries live here and not where the API can import them.
"""

import uuid
from datetime import datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.billing import BillingFetchRun, BillingSource
from app.db.rls import bypass_rls
from app.reconciliation.runs import RUN_IN_PROGRESS_WINDOW, STALE_RUN_ERROR
from app.reconciliation.source_rules import PROVIDER_OF_KIND

# Projects with an LLM span started in [since, until).
_ACTIVE_PROJECTS = text(
    """
    SELECT p.id
    FROM projects AS p
    WHERE EXISTS (
        SELECT 1 FROM spans AS s
        WHERE s.project_id = p.id AND s.kind = 'llm'
          AND s.started_at >= :since AND s.started_at < :until
    )
    ORDER BY p.id
    """
)


async def active_cost_project_ids(
    db: AsyncSession, since: datetime, until: datetime
) -> list[uuid.UUID]:
    """Projects with an LLM span started in `[since, until)`.

    Holds nothing but ids, so it runs with row-level security bypassed, in a transaction it ends
    itself (the bypass is local to it). For the rollup jobs only.
    """
    await bypass_rls(db)
    rows = (await db.execute(_ACTIVE_PROJECTS, {"since": since, "until": until})).scalars().all()
    await db.commit()
    return [uuid.UUID(str(project_id)) for project_id in rows]


async def enabled_api_sources(db: AsyncSession) -> list[tuple[uuid.UUID, datetime | None]]:
    """Every enabled source that fetches from a provider's API, in every organization, with when
    it last fetched successfully (None: never). A deployment has a handful per organization."""
    rows = await db.execute(
        select(BillingSource.id, BillingSource.last_success_at)
        .where(BillingSource.enabled.is_(True), BillingSource.kind.in_(list(PROVIDER_OF_KIND)))
        .order_by(BillingSource.id)
    )
    return [(source_id, last_success_at) for source_id, last_success_at in rows]


async def source_for_job(db: AsyncSession, source_id: uuid.UUID) -> BillingSource | None:
    """The source with this id, whatever its organization; None once it is deleted."""
    found: BillingSource | None = await db.get(BillingSource, source_id)
    return found


async def close_stale_runs(db: AsyncSession) -> int:
    """Close every run still `running` after `RUN_IN_PROGRESS_WINDOW` as failed with
    `STALE_RUN_ERROR`, in every organization and whatever its source; returns how many.

    `open_run` closes a source's dead run only when the source is fetched again, which a disabled
    source never is. A run `adopt_run` restarts meanwhile is re-checked under its row lock and
    kept. The caller commits.
    """
    result = await db.execute(
        update(BillingFetchRun)
        .where(
            BillingFetchRun.status == "running",
            BillingFetchRun.started_at <= func.now() - RUN_IN_PROGRESS_WINDOW,
        )
        .values(status="failed", error=STALE_RUN_ERROR, finished_at=func.now())
    )
    return int(getattr(result, "rowcount", 0))

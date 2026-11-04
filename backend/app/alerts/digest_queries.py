"""Reads of the weekly digest.

Two kinds, with different tenancy rules. The project and member lists span every project, so they
are read with row-level security bypassed; they hold ids, names and addresses only. The figures
of one project (`digest_data`) are read in a transaction bound to that project and never with the
bypass on.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Text, bindparam, func, literal_column, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.digest import MAX_MODELS, DigestData, DigestProject
from app.alerts.metrics import metric_series
from app.alerts.types import Metric, MetricFilters
from app.api.window import TimeWindow
from app.db.models import AlertEvent, Membership, NotificationOutbox, User
from app.db.rls import bypass_rls
from app.rollups.coverage_reads import CoverageState, read_covered
from app.rollups.dirty import HOURLY
from app.services.demo import DEMO_USER_EMAIL

# Projects with the digest on that had at least one span in the week. The hourly rollups answer
# this through `(project_id, bucket_start)` without reading spans.
_ELIGIBLE_PROJECTS = text(
    """
    SELECT p.id, p.name, p.org_id, o.name AS org_name
    FROM projects AS p
    JOIN organizations AS o ON o.id = p.org_id
    WHERE p.weekly_digest_enabled
      AND NOT o.is_demo
      AND EXISTS (
          SELECT 1 FROM span_rollups_hourly AS r
          WHERE r.project_id = p.id AND r.bucket_start >= :start AND r.bucket_start < :end
      )
    ORDER BY p.id
    """
)

_TOP_MODELS = text(
    """
    SELECT coalesce(model, 'unknown model') AS model, sum(cost_usd) AS cost
    FROM span_rollups_hourly
    WHERE project_id = :project_id
      AND bucket_start >= :start AND bucket_start < :end
      AND kind = 'llm'
    GROUP BY coalesce(model, 'unknown model')
    HAVING sum(cost_usd) IS NOT NULL
    ORDER BY cost DESC, model
    LIMIT :n
    """
)


@dataclass(frozen=True, slots=True)
class DigestTarget:
    """A project that gets a digest this week."""

    project_id: uuid.UUID
    name: str
    org_id: uuid.UUID
    org_name: str


async def eligible_projects(db: AsyncSession, week: TimeWindow) -> list[DigestTarget]:
    """Every project to send a digest for. Ends its own transaction (the bypass is local to it)."""
    await bypass_rls(db)
    rows = (await db.execute(_ELIGIBLE_PROJECTS, {"start": week.start, "end": week.end})).all()
    await db.commit()
    return [
        DigestTarget(
            project_id=uuid.UUID(str(row.id)),
            name=row.name,
            org_id=uuid.UUID(str(row.org_id)),
            org_name=row.org_name,
        )
        for row in rows
    ]


async def digest_recipients(db: AsyncSession, org_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
    """Id and address of the organization's members with a verified email who did not turn the
    digest off, in a stable order.

    The shared demo account is never mailed, whether or not its address is verified.
    """
    rows = await db.execute(
        select(User.id, User.email)
        .join(Membership, Membership.user_id == User.id)
        .where(
            Membership.org_id == org_id,
            User.email_verified_at.is_not(None),
            User.weekly_digest_opt_out.is_(False),
            func.lower(User.email) != DEMO_USER_EMAIL,
        )
        .order_by(User.email)
    )
    return [(user_id, str(address)) for user_id, address in rows]


# Spelled exactly as the expression of `notification_outbox_digest_key_idx` (migration 0307), with
# constants rather than bound keys, so the planner can match the index to it.
_DIGEST_KEY = literal_column(
    "((notification_outbox.payload -> 'summary') ->> 'digest_key')", Text()
)


async def queued_digest_keys(db: AsyncSession, since: datetime) -> set[str]:
    """The keys of every digest already queued since `since` (one read for the whole run).

    The key is in each row's `summary`, which the outbox keeps after a row is settled, sent or
    failed. Digest rows are transactional mail (no channel) created after the week ended.
    """
    rows = await db.scalars(
        select(_DIGEST_KEY)
        .where(
            NotificationOutbox.kind == "email",
            NotificationOutbox.channel_id.is_(None),
            NotificationOutbox.created_at >= since,
            _DIGEST_KEY.is_not(None),
        )
        .distinct()
    )
    return set(rows)


async def digest_already_queued(db: AsyncSession, digest_key: str, since: datetime) -> bool:
    """Whether rows for this digest are in the outbox. The check made under the digest's lock,
    after the run-start set may have gone stale; bounded by `since` like that read. Served by
    `notification_outbox_digest_key_idx`, so a run over many projects does not scan the outbox
    once per project."""
    found = await db.scalar(
        select(NotificationOutbox.id)
        .where(
            NotificationOutbox.kind == "email",
            NotificationOutbox.channel_id.is_(None),
            NotificationOutbox.created_at >= since,
            _DIGEST_KEY.op("=")(bindparam("digest_key", digest_key, type_=Text())),
        )
        .limit(1)
    )
    return found is not None


async def lock_digest(db: AsyncSession, digest_key: str) -> None:
    """Serialise two runs that build the same digest, until the transaction ends."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": digest_key}
    )


async def digest_data(
    db: AsyncSession,
    target: DigestTarget,
    week: TimeWindow,
    *,
    now: datetime,
    app_base_url: str,
) -> DigestData:
    """The project's figures for `week` and the week before. The transaction must be bound to
    the project."""
    previous = week.previous()
    spend, previous_spend = await _pair(db, target.project_id, Metric.SPEND, week, previous, now)
    calls, previous_calls = await _pair(
        db, target.project_id, Metric.LLM_CALLS, week, previous, now
    )
    errors, previous_errors = await _pair(
        db, target.project_id, Metric.ERROR_RATE, week, previous, now
    )
    p95, _ = await _pair(db, target.project_id, Metric.P95_MS, week, previous, now)
    return DigestData(
        project=DigestProject(
            id=str(target.project_id),
            name=target.name,
            org_id=str(target.org_id),
            org_name=target.org_name,
        ),
        url=f"{app_base_url.rstrip('/')}/{target.org_id}/{target.project_id}/overview",
        week_start=week.start,
        week_end=week.end,
        spend=spend,
        previous_spend=previous_spend,
        llm_calls=calls,
        previous_llm_calls=previous_calls,
        error_rate=errors,
        previous_error_rate=previous_errors,
        p95_ms=p95,
        top_models=await top_models(db, target.project_id, week),
        alerts_fired=await alerts_fired(db, target.project_id, week),
    )


async def _pair(
    db: AsyncSession,
    project_id: uuid.UUID,
    metric: Metric,
    week: TimeWindow,
    previous: TimeWindow,
    now: datetime,
) -> tuple[Decimal | None, Decimal | None]:
    this_week, last_week = await metric_series(
        db, project_id, metric, [week, previous], MetricFilters(), now=now
    )
    return this_week.value, last_week.value


async def top_models(
    db: AsyncSession, project_id: uuid.UUID, week: TimeWindow
) -> list[tuple[str, Decimal]]:
    """The five models with the highest known cost, most expensive first.

    Ordered by cost in SQL, so an expensive low-volume model is not lost behind busy ones. Costs
    of one model name are added across providers; a model with no priced call has no cost and is
    left out, so the list never shows a 0 that stands for "unknown". The week is whole UTC days,
    so the rollups cover it exactly.
    """
    coverage = CoverageState()
    rows = await read_covered(
        db,
        _TOP_MODELS,
        {"project_id": project_id, "start": week.start, "end": week.end, "n": MAX_MODELS},
        project_id,
        week.start,
        week.end,
        coverage,
        raw=True,
        relevant_consumers=HOURLY,
    )
    if coverage.status != "complete":
        return []
    return [(row.model, Decimal(row.cost)) for row in rows]


async def alerts_fired(db: AsyncSession, project_id: uuid.UUID, week: TimeWindow) -> int:
    """Alert events (rules and budgets) that started during the week."""
    count = await db.scalar(
        select(func.count())
        .select_from(AlertEvent)
        .where(
            AlertEvent.project_id == project_id,
            AlertEvent.started_at >= week.start,
            AlertEvent.started_at < week.end,
        )
    )
    return int(count or 0)

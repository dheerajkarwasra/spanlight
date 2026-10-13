"""Reads and writes of the `evaluate_alerts` job, and of the API paths that close an alert.

Everything runs in a transaction bound to one project, and the explicit `project_id` filters are
a second guard. The job's list of projects, which spans every project, is in
`app.alerts.job_queries`, so the request path never imports code that bypasses row-level
security.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.types import RuleStateName
from app.db.models import AlertEvent, AlertRule, AlertState, Organization, Project


@dataclass(frozen=True, slots=True)
class ProjectNames:
    """What a payload says about the project and its organization."""

    project_id: uuid.UUID
    project_name: str
    org_id: uuid.UUID
    org_name: str


async def project_names(db: AsyncSession, project_id: uuid.UUID) -> ProjectNames | None:
    """The project's and its organization's names; None when the project is gone."""
    row = (
        await db.execute(
            select(Project.name, Project.org_id, Organization.name)
            .join(Organization, Organization.id == Project.org_id)
            .where(Project.id == project_id)
        )
    ).first()
    if row is None:
        return None
    return ProjectNames(project_id=project_id, project_name=row[0], org_id=row[1], org_name=row[2])


async def enabled_rule_ids(db: AsyncSession, project_id: uuid.UUID) -> Sequence[uuid.UUID]:
    """The project's enabled rules, every kind, never-evaluated and longest-ago-evaluated first,
    so a pass cut short by its deadline leaves the rules it reached last for the next one. The
    transaction must be bound to the project."""
    return (
        await db.scalars(
            select(AlertRule.id)
            .outerjoin(AlertState, AlertState.rule_id == AlertRule.id)
            .where(AlertRule.project_id == project_id, AlertRule.enabled.is_(True))
            .order_by(AlertState.last_evaluated_at.asc().nulls_first(), AlertRule.id)
        )
    ).all()


async def lock_rule_for_evaluation(
    db: AsyncSession, project_id: uuid.UUID, rule_id: uuid.UUID
) -> AlertRule | None:
    """The enabled rule, locked until the transaction ends; None when it is gone, disabled, or
    locked by someone else.

    `SKIP LOCKED` makes two overlapping passes split the rules instead of both evaluating one:
    the pass that skips a rule leaves it to the other, so a transition is never applied twice. A
    rule locked by an edit is simply evaluated on the next pass.
    """
    return await db.scalar(
        select(AlertRule)
        .where(
            AlertRule.project_id == project_id,
            AlertRule.id == rule_id,
            AlertRule.enabled.is_(True),
        )
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )


async def save_state(
    db: AsyncSession,
    rule: AlertRule,
    current: AlertState | None,
    *,
    value: Decimal | None,
    now: datetime,
    to_state: RuleStateName | None,
) -> None:
    """Record this evaluation on the rule's state row, creating it on the first one.

    `last_value` and `last_evaluated_at` change on every pass; `state` and `since` only with a
    transition (`to_state`). The rule's row lock makes the read-then-write safe.
    """
    if current is None:
        current = AlertState(
            rule_id=rule.id, project_id=rule.project_id, state=RuleStateName.OK, since=None
        )
        db.add(current)
    current.last_value = value
    current.last_evaluated_at = now
    if to_state is not None:
        current.state = to_state
        current.since = now
    await db.flush()


async def open_event(db: AsyncSession, rule: AlertRule) -> AlertEvent | None:
    """The rule's open event, if any (at most one, by the partial unique index)."""
    return await db.scalar(
        select(AlertEvent).where(
            AlertEvent.project_id == rule.project_id,
            AlertEvent.rule_id == rule.id,
            AlertEvent.resolved_at.is_(None),
        )
    )


async def resolve_open_event(
    db: AsyncSession, rule: AlertRule, now: datetime, *, not_before_start: bool = False
) -> AlertEvent | None:
    """Close the rule's open event at `now` and return it; None when no event is open.

    `not_before_start` closes it at its own start instead when that is later than `now` (an
    event opened by a pass whose clock ran ahead), so the close cannot break the
    `resolved_at >= started_at` check.
    """
    resolved_at = func.greatest(AlertEvent.started_at, now) if not_before_start else now
    event = await db.scalar(
        update(AlertEvent)
        .where(
            AlertEvent.project_id == rule.project_id,
            AlertEvent.rule_id == rule.id,
            AlertEvent.resolved_at.is_(None),
        )
        .values(resolved_at=resolved_at)
        .returning(AlertEvent)
        .execution_options(populate_existing=True)
    )
    return event

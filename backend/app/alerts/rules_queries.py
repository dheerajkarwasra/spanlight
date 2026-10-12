"""Reads and locks for alert rules and events.

`alert_rules`, `alert_states` and `alert_events` are under row-level security, so every query
here runs in a transaction bound to the project; the explicit `project_id` filters are a second
guard. Budget rules are left out of every rule read: their budget manages them.
"""

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime

from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.types import AlertRuleKind, RuleStateName
from app.db.models import AlertChannel, AlertEvent, AlertRule, AlertState, User

EventRow = tuple[AlertEvent, AlertRule, User | None]


def _people_rules(project_id: uuid.UUID) -> Select[AlertRule, AlertState]:
    return (
        select(AlertRule, AlertState)
        .outerjoin(AlertState, AlertState.rule_id == AlertRule.id)
        .where(AlertRule.project_id == project_id, AlertRule.kind != AlertRuleKind.BUDGET)
    )


async def list_rules(
    db: AsyncSession, project_id: uuid.UUID
) -> list[tuple[AlertRule, AlertState | None]]:
    """The project's rules with their state: firing first, then by name.

    Unpaginated: a project has at most 100 rules.
    """
    firing_first = case((AlertState.state == RuleStateName.FIRING, 0), else_=1)
    rows = await db.execute(
        _people_rules(project_id).order_by(firing_first, AlertRule.name, AlertRule.id)
    )
    return [(rule, state) for rule, state in rows]


async def get_rule(
    db: AsyncSession, project_id: uuid.UUID, rule_id: uuid.UUID
) -> tuple[AlertRule, AlertState | None] | None:
    row = (await db.execute(_people_rules(project_id).where(AlertRule.id == rule_id))).first()
    return None if row is None else (row[0], row[1])


async def get_state(db: AsyncSession, rule_id: uuid.UUID) -> AlertState | None:
    return await db.get(AlertState, rule_id)


async def lock_rule(
    db: AsyncSession, project_id: uuid.UUID, rule_id: uuid.UUID
) -> AlertRule | None:
    """The rule, locked until the transaction ends, so two edits do not interleave."""
    return await db.scalar(
        select(AlertRule)
        .where(
            AlertRule.project_id == project_id,
            AlertRule.id == rule_id,
            AlertRule.kind != AlertRuleKind.BUDGET,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def count_rules(db: AsyncSession, project_id: uuid.UUID) -> int:
    """How many rules people manage in the project. Callers hold the project's lock."""
    count = await db.scalar(
        select(func.count())
        .select_from(AlertRule)
        .where(AlertRule.project_id == project_id, AlertRule.kind != AlertRuleKind.BUDGET)
    )
    return int(count or 0)


async def org_channel_ids(
    db: AsyncSession, org_id: uuid.UUID, channel_ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of `channel_ids` are channels of the organization."""
    wanted = list(channel_ids)
    if not wanted:
        return set()
    rows = await db.scalars(
        select(AlertChannel.id).where(AlertChannel.org_id == org_id, AlertChannel.id.in_(wanted))
    )
    return set(rows)


def _events(project_id: uuid.UUID) -> Select[AlertEvent, AlertRule, User]:
    return (
        select(AlertEvent, AlertRule, User)
        .join(AlertRule, AlertRule.id == AlertEvent.rule_id)
        .outerjoin(User, User.id == AlertEvent.acknowledged_by)
        .where(AlertEvent.project_id == project_id)
    )


async def list_events(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    rule_id: uuid.UUID | None,
    firing: bool | None,
    after: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> Sequence[EventRow]:
    """Events newest first (`started_at desc, id desc`), budget rules' included.

    `firing` True keeps open events, False resolved ones, None both. `after` is the sort key of
    the last event of the previous page. Reads `limit + 1` rows so the caller can tell whether
    another page follows.
    """
    query = _events(project_id)
    if rule_id is not None:
        query = query.where(AlertEvent.rule_id == rule_id)
    if firing is True:
        query = query.where(AlertEvent.resolved_at.is_(None))
    elif firing is False:
        query = query.where(AlertEvent.resolved_at.is_not(None))
    if after is not None:
        started_at, event_id = after
        query = query.where(
            or_(
                AlertEvent.started_at < started_at,
                and_(AlertEvent.started_at == started_at, AlertEvent.id < event_id),
            )
        )
    query = query.order_by(AlertEvent.started_at.desc(), AlertEvent.id.desc()).limit(limit + 1)
    rows = await db.execute(query)
    return [(event, rule, user) for event, rule, user in rows]


async def get_event(
    db: AsyncSession, project_id: uuid.UUID, event_id: uuid.UUID
) -> EventRow | None:
    row = (await db.execute(_events(project_id).where(AlertEvent.id == event_id))).first()
    return None if row is None else (row[0], row[1], row[2])


async def lock_event(
    db: AsyncSession, project_id: uuid.UUID, event_id: uuid.UUID
) -> AlertEvent | None:
    """The event, locked until the transaction ends, so two acknowledgements do not both pass."""
    return await db.scalar(
        select(AlertEvent)
        .where(AlertEvent.project_id == project_id, AlertEvent.id == event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

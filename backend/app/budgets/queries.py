"""Reads and locks for budgets and their rules.

`budgets`, `alert_rules` and `alert_states` are under row-level security, so every query here
runs in a transaction bound to the project; the explicit `project_id` filters are a second guard.
"""

import uuid

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.types import AlertRuleKind
from app.db.models import AlertRule, AlertState, Budget, GatewayKey, Trace

BudgetRow = tuple[Budget, AlertState | None]


def _with_state(project_id: uuid.UUID) -> Select[Budget, AlertState]:
    return (
        select(Budget, AlertState)
        .outerjoin(AlertState, AlertState.rule_id == Budget.rule_id)
        .where(Budget.project_id == project_id)
    )


async def list_budgets(db: AsyncSession, project_id: uuid.UUID) -> list[BudgetRow]:
    """The project's budgets with their rule's state, by name. Unpaginated: at most 50."""
    rows = await db.execute(_with_state(project_id).order_by(Budget.name, Budget.id))
    return [(budget, state) for budget, state in rows]


async def get_budget(
    db: AsyncSession, project_id: uuid.UUID, budget_id: uuid.UUID
) -> BudgetRow | None:
    row = (await db.execute(_with_state(project_id).where(Budget.id == budget_id))).first()
    return None if row is None else (row[0], row[1])


async def get_state(db: AsyncSession, rule_id: uuid.UUID) -> AlertState | None:
    return await db.get(AlertState, rule_id, populate_existing=True)


async def lock_budget(
    db: AsyncSession, project_id: uuid.UUID, budget_id: uuid.UUID
) -> tuple[Budget, AlertRule] | None:
    """The budget and its rule, both locked until the transaction ends.

    Rule first, as the evaluation job locks it, then the budget, so an edit and an evaluation
    wait for each other instead of interleaving.
    """
    rule_id = await db.scalar(
        select(Budget.rule_id).where(Budget.project_id == project_id, Budget.id == budget_id)
    )
    if rule_id is None:
        return None
    rule = await db.scalar(
        select(AlertRule)
        .where(
            AlertRule.project_id == project_id,
            AlertRule.id == rule_id,
            AlertRule.kind == AlertRuleKind.BUDGET,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    budget = await db.scalar(
        select(Budget)
        .where(Budget.project_id == project_id, Budget.id == budget_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if rule is None or budget is None:
        return None
    return budget, rule


async def budget_for_rule(db: AsyncSession, rule: AlertRule) -> Budget | None:
    """The budget that owns `rule`; None for any other rule."""
    return await db.scalar(
        select(Budget).where(Budget.project_id == rule.project_id, Budget.rule_id == rule.id)
    )


async def count_budgets(db: AsyncSession, project_id: uuid.UUID) -> int:
    """How many budgets the project has. Callers hold the project's lock."""
    count = await db.scalar(
        select(func.count()).select_from(Budget).where(Budget.project_id == project_id)
    )
    return int(count or 0)


async def is_gateway_key_of(db: AsyncSession, project_id: uuid.UUID, key_id: uuid.UUID) -> bool:
    """Whether `key_id` is a gateway key of the project, revoked or not."""
    found = await db.scalar(
        select(GatewayKey.id).where(GatewayKey.project_id == project_id, GatewayKey.id == key_id)
    )
    return found is not None


async def forget_state(db: AsyncSession, rule: AlertRule) -> None:
    """Drop the rule's state row: its last value measured something the budget no longer is."""
    await db.execute(
        delete(AlertState).where(
            AlertState.project_id == rule.project_id, AlertState.rule_id == rule.id
        )
    )


async def trace_dimensions(
    db: AsyncSession, project_id: uuid.UUID, trace_id: str
) -> tuple[str | None, dict[str, str]] | None:
    """The stored trace's name and metadata, for the guard of a call that joins it (spec §8.3).

    One primary-key read; None when no span of the trace has been stored yet.
    """
    row = (
        await db.execute(
            select(Trace.name, Trace.metadata_).where(
                Trace.project_id == project_id, Trace.trace_id == trace_id
            )
        )
    ).first()
    return None if row is None else (row[0], dict(row[1]))

"""The hidden alert rule a budget owns, kept in step with the budget.

A budget is evaluated by an `alert_rules` row of kind `budget`: metric `spend`, comparator `gt`,
the budget's amount as the threshold, no window (the evaluation job reads the budget's period
instead), no cooldown, the budget's scope as the filter, and the budget's name, channels and
enabled flag. People never edit it directly: the rules API treats it as missing. The budget
service writes the budget and its rule in one transaction, through these helpers.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.types import AlertRuleKind, Comparator, Metric
from app.budgets.schemas import BudgetSpec
from app.budgets.scope_parse import parse_metadata_scope
from app.budgets.types import BudgetScope
from app.db.models import AlertRule, BudgetCounter, BudgetReservation

# The filter key each one-value scope sets on the rule (`app.alerts.rules.metric_filters` reads
# them). A `metadata` scope sets `metadata_key` and `metadata_value` instead, split from its
# `scope_id` once here so the evaluation never parses it again.
_SCOPE_FILTER = {
    BudgetScope.GATEWAY_KEY: "source_key_id",
    BudgetScope.USER: "external_user_id",
    BudgetScope.MODEL: "model",
    BudgetScope.TRACE_NAME: "trace_name",
}


def scope_filters(scope: BudgetScope, scope_id: str | None) -> dict[str, str]:
    """The rule's `filters` for a budget scope: none for the whole project."""
    if scope_id is None:
        return {}
    if scope is BudgetScope.METADATA:
        key, value = parse_metadata_scope(scope_id)
        return {"metadata_key": key, "metadata_value": value}
    name = _SCOPE_FILTER.get(scope)
    if name is None:
        return {}
    return {name: scope_id}


def budget_rule_values(spec: BudgetSpec) -> dict[str, Any]:
    """The `alert_rules` columns a budget decides."""
    return {
        "name": spec.name,
        "threshold": spec.amount_usd,
        "filters": scope_filters(spec.scope, spec.scope_id),
        "channel_ids": list(spec.channel_ids),
        "enabled": spec.enabled,
    }


async def create_budget_rule(
    db: AsyncSession,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    spec: BudgetSpec,
    *,
    now: datetime,
) -> AlertRule:
    """Insert the hidden rule for a new budget and flush it, so the budget can point at it."""
    rule = AlertRule(
        project_id=project_id,
        kind=AlertRuleKind.BUDGET,
        metric=Metric.SPEND,
        comparator=Comparator.GT,
        window_minutes=None,
        baseline_windows=None,
        sensitivity=None,
        cooldown_minutes=0,
        muted_until=None,
        created_by=actor_id,
        created_at=now,
        updated_at=now,
        **budget_rule_values(spec),
    )
    db.add(rule)
    await db.flush()
    return rule


def sync_budget_rule(rule: AlertRule, spec: BudgetSpec, *, now: datetime) -> bool:
    """Copy what the budget decides onto its rule; returns whether anything changed."""
    changed = False
    for column, value in budget_rule_values(spec).items():
        if getattr(rule, column) != value:
            setattr(rule, column, value)
            changed = True
    if changed:
        rule.updated_at = now
    return changed


async def forget_counters(db: AsyncSession, budget_id: uuid.UUID) -> None:
    """Drop the budget's gateway counters and reservations (`app.budgets.reserve`) when its scope
    or period changes: the spend they count belonged to the old one, as `forget_state` does for
    the rule's last measured value."""
    for table in (BudgetReservation, BudgetCounter):
        await db.execute(
            delete(table)
            .where(table.budget_id == budget_id)
            .execution_options(synchronize_session=False)
        )

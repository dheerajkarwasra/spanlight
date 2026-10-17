"""Budgets: create, edit and delete a budget together with the hidden rule that evaluates it.
Callers commit.

Every write runs in a transaction bound to the project (row-level security) and takes the
project's write lock first (`lock_project_for_write`, through the router), then the budget's rule
and the budget (`queries.lock_budget`). The project lock conflicts with itself, so two creates in
one project run one after the other and cannot both pass the budget limit.

A budget and its rule always change in the same transaction. An edit that changes what a firing
budget measures or caps (amount, scope, period) or disables it resolves the alert first and
sends `alert.resolved`, as for rules; a new scope or period also drops the rule's last measured
spend, which belonged to the old one, so the budget shows no spend until the next evaluation.

Audit metadata holds the budget's name, scope kind, period and action and the names of the
fields that changed; never `scope_id`, which can be an end user's identifier.
"""

import uuid
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.evaluation import close_firing_alert
from app.alerts.rule_errors import UnknownChannelError
from app.alerts.rules_queries import org_channel_ids
from app.budgets import queries
from app.budgets.errors import (
    BudgetLimitError,
    BudgetNameTakenError,
    BudgetNotFoundError,
    UnknownScopeError,
)
from app.budgets.rule_sync import create_budget_rule, sync_budget_rule
from app.budgets.schemas import BudgetPatch, BudgetSpec
from app.budgets.scope_parse import parse_metadata_scope, parse_trace_name_scope
from app.budgets.types import BudgetScope
from app.config import Settings
from app.core.redact import redact_text
from app.db.errors import violated_constraint
from app.db.models import AlertRule, AuditAction, Budget
from app.services.audit import record_audit

MAX_BUDGETS_PER_PROJECT = 50
NAME_CONSTRAINT = "budgets_project_name_key"
# Changing any of these makes a firing alert describe a cap that no longer exists.
REDEFINING_COLUMNS = frozenset({"amount_usd", "scope", "scope_id", "period"})
# Changing any of these makes the last measured spend belong to something else.
REMEASURING_COLUMNS = frozenset({"scope", "scope_id", "period"})


async def create_budget(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    spec: BudgetSpec,
    *,
    now: datetime,
    ip: str | None = None,
) -> Budget:
    """Store a new budget and its rule. The caller holds the project's write lock.

    Raises `UnknownChannelError`, `UnknownScopeError`, `BudgetLimitError` and
    `BudgetNameTakenError`.
    """
    await _check_channels(db, org_id, spec)
    spec = await _checked_scope(db, project_id, spec)
    if await queries.count_budgets(db, project_id) >= MAX_BUDGETS_PER_PROJECT:
        raise BudgetLimitError(MAX_BUDGETS_PER_PROJECT)
    rule = await create_budget_rule(db, project_id, actor_id, spec, now=now)
    budget = Budget(
        project_id=project_id,
        rule_id=rule.id,
        created_by=actor_id,
        created_at=now,
        updated_at=now,
        **spec.column_values(),
    )
    db.add(budget)
    await _flush_named(db)
    await _audit(db, org_id, budget, AuditAction.BUDGET_CREATE, actor_id, ip=ip)
    return budget


async def update_budget(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    budget_id: uuid.UUID,
    actor_id: uuid.UUID,
    patch: BudgetPatch,
    *,
    now: datetime,
    ip: str | None = None,
    settings: Settings | None = None,
) -> Budget:
    """Apply a partial edit to the budget and its rule. The caller holds the project's lock.

    Channels are checked only when sent and the scope only when it changes, so renaming a budget
    whose gateway key or channel was deleted still works. An edit that changes nothing writes no
    audit event. Raises `BudgetNotFoundError`, `UnknownChannelError`, `UnknownScopeError` and
    `BudgetNameTakenError`.
    """
    found = await queries.lock_budget(db, project_id, budget_id)
    if found is None:
        raise BudgetNotFoundError
    budget, rule = found
    spec = await _checked_patch(db, org_id, project_id, patch, spec_of(budget))
    values = spec.column_values()
    changed = [column for column, value in values.items() if getattr(budget, column) != value]
    if not changed:
        return budget
    await _settle_alert(db, rule, spec, changed, now=now, settings=settings)
    for column in changed:
        setattr(budget, column, values[column])
    budget.updated_at = now
    sync_budget_rule(rule, spec, now=now)
    await _flush_named(db)
    await _audit(
        db, org_id, budget, AuditAction.BUDGET_UPDATE, actor_id, ip=ip, extra={"changed": changed}
    )
    return budget


async def delete_budget(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    budget_id: uuid.UUID,
    actor_id: uuid.UUID,
    *,
    now: datetime,
    ip: str | None = None,
    settings: Settings | None = None,
) -> None:
    """Delete the budget with its rule, state and events. The caller holds the project's lock.

    A firing budget is resolved first, queuing `alert.resolved`, so receivers close their
    incident. Raises `BudgetNotFoundError`.
    """
    found = await queries.lock_budget(db, project_id, budget_id)
    if found is None:
        raise BudgetNotFoundError
    budget, rule = found
    await close_firing_alert(db, rule, now, settings=settings)
    await _audit(db, org_id, budget, AuditAction.BUDGET_DELETE, actor_id, ip=ip)
    await db.delete(budget)
    await db.flush()
    await db.delete(rule)
    await db.flush()


async def _checked_patch(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    patch: BudgetPatch,
    current: BudgetSpec,
) -> BudgetSpec:
    """The budget after the edit, with the channels checked when sent and the scope when sent."""
    spec = patch.merged_with(current)
    if "channel_ids" in patch.model_fields_set:
        await _check_channels(db, org_id, spec)
    if "scope" in patch.model_fields_set:
        spec = await _checked_scope(db, project_id, spec)
    return spec


async def _settle_alert(
    db: AsyncSession,
    rule: AlertRule,
    spec: BudgetSpec,
    changed: list[str],
    *,
    now: datetime,
    settings: Settings | None,
) -> None:
    """Before an edit is applied: resolve a firing alert the edit makes stale, and drop a last
    measured spend that belonged to the old scope or period."""
    if REDEFINING_COLUMNS.intersection(changed) or ("enabled" in changed and not spec.enabled):
        # Before the change: the resolve payload describes the budget that fired.
        await close_firing_alert(db, rule, now, settings=settings)
    if REMEASURING_COLUMNS.intersection(changed):
        await queries.forget_state(db, rule)


def spec_of(budget: Budget) -> BudgetSpec:
    """The budget as stored, as a whole spec."""
    return BudgetSpec.model_validate(
        {
            "name": budget.name,
            "scope": budget.scope,
            "scope_id": budget.scope_id,
            "period": budget.period,
            "amount_usd": budget.amount_usd,
            "action": budget.action,
            "enabled": budget.enabled,
            "channel_ids": list(budget.channel_ids),
        }
    )


async def _check_channels(db: AsyncSession, org_id: uuid.UUID, spec: BudgetSpec) -> None:
    """Every channel the budget names is a channel of the organization."""
    known = await org_channel_ids(db, org_id, spec.channel_ids)
    unknown = [channel_id for channel_id in spec.channel_ids if channel_id not in known]
    if unknown:
        raise UnknownChannelError(unknown)


async def _checked_scope(db: AsyncSession, project_id: uuid.UUID, spec: BudgetSpec) -> BudgetSpec:
    """The spec with its `scope_id` checked for the scope and normalised: a `gateway_key` id is
    a key of the project, a `metadata` scope parses as `key=value` with a value ingest stores as
    sent, a `trace_name` is trimmed. Raises `UnknownScopeError` otherwise."""
    scope_id = spec.scope_id or ""
    if spec.scope is BudgetScope.METADATA:
        try:
            _, value = parse_metadata_scope(scope_id)
        except ValueError:
            raise UnknownScopeError(UnknownScopeError.METADATA) from None
        if redact_text(value) != value:
            raise UnknownScopeError(UnknownScopeError.REDACTED)
        return spec
    if spec.scope is BudgetScope.TRACE_NAME:
        try:
            name = parse_trace_name_scope(scope_id)
        except ValueError:
            raise UnknownScopeError(UnknownScopeError.TRACE_NAME) from None
        return spec.model_copy(update={"scope_id": name})
    if spec.scope is not BudgetScope.GATEWAY_KEY:
        return spec
    try:
        key_id = uuid.UUID(scope_id)
    except ValueError:
        raise UnknownScopeError from None
    if not await queries.is_gateway_key_of(db, project_id, key_id):
        raise UnknownScopeError
    return spec.model_copy(update={"scope_id": str(key_id)})


async def _flush_named(db: AsyncSession) -> None:
    """Flush pending budget changes; a name another budget of the project has is
    `BudgetNameTakenError`."""
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as error:
        if violated_constraint(error) == NAME_CONSTRAINT:
            raise BudgetNameTakenError from None
        raise


async def _audit(
    db: AsyncSession,
    org_id: uuid.UUID,
    budget: Budget,
    action: AuditAction,
    actor_id: uuid.UUID,
    *,
    ip: str | None,
    extra: dict[str, object] | None = None,
) -> None:
    metadata: dict[str, object] = {
        "name": budget.name,
        "scope": budget.scope.value,
        "period": budget.period.value,
        "action": budget.action.value,
        **(extra or {}),
    }
    await record_audit(
        db,
        org_id=org_id,
        actor_user_id=actor_id,
        action=action,
        target_type="budget",
        target_id=budget.id,
        ip=ip,
        metadata=metadata,
    )

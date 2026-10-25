"""Evaluators: create, edit and delete them, and read a judge's budget. Callers commit.

Every write runs in a transaction bound to the project (row-level security); an edit takes the
evaluator's row lock. A config is checked against the project and the organization before it is
stored (`app.evals.validation`). Audit metadata holds the name, the kind and the changed member
names; never config values such as a credential id or a webhook URL.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.errors import violated_constraint
from app.db.models import AuditAction, Evaluator
from app.evals import queries
from app.evals.budget import month_start, remaining
from app.evals.config import EvaluatorPatch, EvaluatorSpec, parse_config
from app.evals.config_resolve import patched_config_in, resolve_config
from app.evals.errors import EvaluatorNameTakenError, EvaluatorNotFoundError
from app.evals.reservation import MonthSpend, month_spend, month_spends
from app.evals.validation import check_config
from app.services.audit import record_audit

NAME_CONSTRAINT = "evaluators_project_name_key"
# The members whose change re-runs the config checks (references, prices, budget rules).
CHECKED_MEMBERS = frozenset({"config", "sampling_rate", "monthly_budget_usd"})
# The config fields that make a judge a different judge: changing one resets its agreement.
JUDGE_IDENTITY = ("model", "prompt_version_id", "temperature")


@dataclass(frozen=True, slots=True)
class Actor:
    org_id: uuid.UUID
    user_id: uuid.UUID
    ip: str | None


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    """A judge's month so far. `monthly_budget_usd` and `remaining_usd` are None without one."""

    monthly_budget_usd: Decimal | None
    spent_usd: Decimal
    reserved_usd: Decimal
    remaining_usd: Decimal | None
    month_start: datetime


@dataclass(frozen=True, slots=True)
class BudgetSummary:
    """A judge's budget and its month so far, as an evaluator list shows it."""

    monthly_budget_usd: Decimal
    spent_usd: Decimal
    reserved_usd: Decimal


async def create_evaluator(
    db: AsyncSession, project_id: uuid.UUID, actor: Actor, spec: EvaluatorSpec, *, now: datetime
) -> Evaluator:
    """Check and store a new evaluator. Raises `EvaluatorNameTakenError`, `EvaluatorConfigError`
    and `EvaluatorReferenceNotFoundError`. A judge template becomes the project's prompt
    `judges/<name>` (created on first use)."""
    config = await resolve_config(db, actor.org_id, project_id, actor.user_id, spec.config, now=now)
    await check_config(
        db,
        org_id=actor.org_id,
        project_id=project_id,
        config=config,
        sampling_rate=spec.sampling_rate,
        monthly_budget_usd=spec.monthly_budget_usd,
        now=now,
    )
    evaluator = Evaluator(
        project_id=project_id,
        name=spec.name,
        kind=config.kind,
        config=config.model_dump(mode="json"),
        sampling_rate=spec.sampling_rate,
        filters=spec.filters.model_dump(mode="json"),
        monthly_budget_usd=spec.monthly_budget_usd,
        enabled=spec.enabled,
        created_by=actor.user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(evaluator)
    await _flush_named(db)
    await _audit(db, actor, AuditAction.EVALUATOR_CREATE, evaluator, None)
    return evaluator


async def update_evaluator(
    db: AsyncSession,
    project_id: uuid.UUID,
    evaluator_id: uuid.UUID,
    actor: Actor,
    patch: EvaluatorPatch,
    *,
    now: datetime,
) -> Evaluator:
    """Apply the members `patch` sets. A change to the config, the sampling rate or the budget is
    checked as a create would be; a rename, a filter change or switching on and off is not, so an
    evaluator whose credential or channel went away can still be disabled. Raises
    `EvaluatorNotFoundError` and what `create_evaluator` raises."""
    evaluator = await _locked(db, project_id, evaluator_id)
    changed = patch.model_fields_set
    if changed & CHECKED_MEMBERS:
        await _apply_checked(db, evaluator, actor, patch, now)
    if patch.name is not None:
        evaluator.name = patch.name
    if patch.enabled is not None:
        evaluator.enabled = patch.enabled
    if patch.filters is not None:
        evaluator.filters = patch.filters.model_dump(mode="json")
    evaluator.updated_at = now
    await _flush_named(db)
    await _audit(db, actor, AuditAction.EVALUATOR_UPDATE, evaluator, sorted(changed))
    return evaluator


async def _apply_checked(
    db: AsyncSession, evaluator: Evaluator, actor: Actor, patch: EvaluatorPatch, now: datetime
) -> None:
    """Merge the config, sampling rate and budget of `patch`, check the result, and store it.
    A change to the judge itself (`JUDGE_IDENTITY`) stamps `config_changed_at`."""
    config = await resolve_config(
        db,
        actor.org_id,
        evaluator.project_id,
        actor.user_id,
        patched_config_in(evaluator, patch.config),
        now=now,
    )
    sampling_rate = evaluator.sampling_rate if patch.sampling_rate is None else patch.sampling_rate
    budget = (
        patch.monthly_budget_usd
        if "monthly_budget_usd" in patch.model_fields_set
        else evaluator.monthly_budget_usd
    )
    await check_config(
        db,
        org_id=actor.org_id,
        project_id=evaluator.project_id,
        config=config,
        sampling_rate=sampling_rate,
        monthly_budget_usd=budget,
        now=now,
    )
    before = parse_config(evaluator.config)
    if any(getattr(before, f, None) != getattr(config, f, None) for f in JUDGE_IDENTITY):
        evaluator.config_changed_at = now
    evaluator.config = config.model_dump(mode="json")
    evaluator.sampling_rate = sampling_rate
    evaluator.monthly_budget_usd = budget


async def delete_evaluator(
    db: AsyncSession, project_id: uuid.UUID, evaluator_id: uuid.UUID, actor: Actor
) -> None:
    """Delete the evaluator with its runs; its scores stay, unlinked. Raises
    `EvaluatorNotFoundError`."""
    evaluator = await _locked(db, project_id, evaluator_id)
    await db.delete(evaluator)
    await db.flush()
    await _audit(db, actor, AuditAction.EVALUATOR_DELETE, evaluator, None)


async def budget_status(db: AsyncSession, evaluator: Evaluator, now: datetime) -> BudgetStatus:
    """The evaluator's month so far: settled spend, open reservations and what is left."""
    spend = await month_spend(db, evaluator.id, now)
    budget = evaluator.monthly_budget_usd
    return BudgetStatus(
        monthly_budget_usd=budget,
        spent_usd=spend.spent,
        reserved_usd=spend.reserved,
        remaining_usd=None if budget is None else remaining(budget, spend.committed),
        month_start=month_start(now),
    )


async def budget_summaries(
    db: AsyncSession, evaluators: Sequence[Evaluator], now: datetime
) -> dict[uuid.UUID, BudgetSummary]:
    """The budget summary of every evaluator that has a budget (judges), by id. One grouped
    query for all of them."""
    budgeted = {e.id: e.monthly_budget_usd for e in evaluators if e.monthly_budget_usd is not None}
    spends = await month_spends(db, list(budgeted), now)
    summaries: dict[uuid.UUID, BudgetSummary] = {}
    for evaluator_id, budget in budgeted.items():
        spend = spends.get(evaluator_id, MonthSpend(Decimal(0), Decimal(0)))
        summaries[evaluator_id] = BudgetSummary(budget, spend.spent, spend.reserved)
    return summaries


async def _locked(db: AsyncSession, project_id: uuid.UUID, evaluator_id: uuid.UUID) -> Evaluator:
    evaluator = await queries.get_evaluator(db, project_id, evaluator_id, lock=True)
    if evaluator is None:
        raise EvaluatorNotFoundError
    return evaluator


async def _flush_named(db: AsyncSession) -> None:
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError as error:
        if violated_constraint(error) == NAME_CONSTRAINT:
            raise EvaluatorNameTakenError from None
        raise


async def _audit(
    db: AsyncSession,
    actor: Actor,
    action: AuditAction,
    evaluator: Evaluator,
    changed: list[str] | None,
) -> None:
    metadata: dict[str, Any] = {"name": evaluator.name, "kind": evaluator.kind}
    if changed is not None:
        metadata["changed"] = changed
    await record_audit(
        db,
        org_id=actor.org_id,
        actor_user_id=actor.user_id,
        action=action,
        target_type="evaluator",
        target_id=evaluator.id,
        ip=actor.ip,
        metadata=metadata,
    )

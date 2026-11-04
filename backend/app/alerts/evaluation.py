"""Evaluate one project's alert rules, each in a transaction of its own.

For one rule the transaction locks the rule, reads its metric, runs the state machine, writes the
state row (every pass), and on a state change writes the event and queues every notification.
All of that commits together or not at all: a rule whose evaluation fails is rolled back alone,
counted as an error, and the project's other rules still run.
"""

import enum
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.evaluate import Transition, clear_since, evaluate_rule
from app.alerts.evaluation_guards import fired_last_period, judged_by_newer_pass, last_reading
from app.alerts.evaluation_queries import (
    ProjectNames,
    enabled_rule_ids,
    lock_rule_for_evaluation,
    open_event,
    project_names,
    resolve_open_event,
    save_state,
)
from app.alerts.evaluation_read import read_rule
from app.alerts.metrics import MetricCache
from app.alerts.notify import notify_channels
from app.alerts.payload import BudgetPayload
from app.alerts.rules import rule_condition, rule_state
from app.alerts.transitions import apply_transition, budget_payload
from app.alerts.types import AlertRuleKind, RuleStateName
from app.budgets.queries import budget_for_rule
from app.config import get_settings
from app.core.observability import ALERT_RULES_EVALUATED, ALERT_TRANSITIONS
from app.db.errors import violated_constraint
from app.db.models import AlertRule, AlertState
from app.db.rls import bind_project

if TYPE_CHECKING:
    from app.config import Settings

logger = structlog.get_logger(__name__)

# The partial unique index that allows one open event per rule (migration 0303).
_ONE_OPEN_EVENT_INDEX = "alert_events_one_open_per_rule_key"

CloseReason = Literal["edit", "period_rollover"]
_CLOSED_EVENTS: dict[CloseReason, str] = {
    "edit": "alert_closed_by_edit",
    "period_rollover": "alert_closed_by_period_rollover",
}


class RuleOutcome(enum.StrEnum):
    OK = "ok"
    # The value was unknown, or an anomaly rule had no baseline yet: nothing could change.
    NO_DATA = "no_data"
    ERROR = "error"
    # Not evaluated by this pass: disabled or deleted meanwhile, locked by another pass or an
    # edit, a budget rule whose budget is gone, already evaluated by a newer pass, or already
    # transitioned by another pass.
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class RuleResult:
    outcome: RuleOutcome
    transition: Transition | None = None
    rule: AlertRule | None = None


@dataclass(slots=True)
class ProjectOutcome:
    """One project's pass: rules evaluated (errors included), state changes, errors, and the
    rules left for the next pass when the run's deadline came first."""

    evaluated: int = 0
    transitions: int = 0
    errors: int = 0
    left: int = 0

    def record(self, result: RuleResult) -> None:
        if result.outcome is RuleOutcome.SKIPPED:
            return
        self.evaluated += 1
        self.errors += result.outcome is RuleOutcome.ERROR
        self.transitions += result.transition is not None


async def evaluate_project(
    db: AsyncSession,
    project_id: uuid.UUID,
    now: datetime,
    cache: MetricCache,
    *,
    settings: "Settings",
    deadline: float | None = None,
) -> ProjectOutcome:
    """Evaluate every enabled rule of the project at `now`. Commits once per rule.

    Rules evaluated longest ago go first, and no rule starts once `time.monotonic()` passes
    `deadline`, so a slow project leaves its other rules to lead the next pass.
    """
    await bind_project(db, project_id)
    names = await project_names(db, project_id)
    rule_ids = list(await enabled_rule_ids(db, project_id)) if names is not None else []
    await db.commit()
    outcome = ProjectOutcome()
    if names is None:
        return outcome
    for index, rule_id in enumerate(rule_ids):
        if deadline is not None and time.monotonic() >= deadline:
            outcome.left = len(rule_ids) - index
            break
        outcome.record(await _evaluate_alone(db, names, rule_id, now, cache, settings))
    return outcome


async def _evaluate_alone(
    db: AsyncSession,
    names: ProjectNames,
    rule_id: uuid.UUID,
    now: datetime,
    cache: MetricCache,
    settings: "Settings",
) -> RuleResult:
    """One rule in its own transaction; a failure rolls back this rule only."""
    try:
        result = await _evaluate(db, names, rule_id, now, cache, settings)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        if violated_constraint(exc) != _ONE_OPEN_EVENT_INDEX:
            return _failed(rule_id, exc)
        # Another pass opened the event first: the state change is already applied.
        logger.warning("alert_event_already_open", rule_id=str(rule_id))
        return RuleResult(RuleOutcome.SKIPPED)
    except Exception as exc:  # noqa: BLE001 - one broken rule must not stop the others
        await db.rollback()
        return _failed(rule_id, exc)
    _count(result)
    return result


def _failed(rule_id: uuid.UUID, exc: BaseException) -> RuleResult:
    logger.error(
        "alert_rule_evaluation_failed",
        rule_id=str(rule_id),
        error_type=type(exc).__name__,
        exc_info=exc,
    )
    ALERT_RULES_EVALUATED.labels(outcome=RuleOutcome.ERROR.value).inc()
    return RuleResult(RuleOutcome.ERROR)


def _count(result: RuleResult) -> None:
    """Metrics and the transition log line for a committed rule; a skipped one is not counted."""
    if result.outcome is RuleOutcome.SKIPPED:
        return
    ALERT_RULES_EVALUATED.labels(outcome=result.outcome.value).inc()
    if result.transition is None or result.rule is None:
        return
    to_state = result.transition.to_state.value
    ALERT_TRANSITIONS.labels(kind=result.rule.kind.value, to_state=to_state).inc()
    logger.info(
        "alert_transition",
        rule_id=str(result.rule.id),
        project_id=str(result.rule.project_id),
        to_state=to_state,
    )


async def _evaluate(
    db: AsyncSession,
    names: ProjectNames,
    rule_id: uuid.UUID,
    now: datetime,
    cache: MetricCache,
    settings: "Settings",
) -> RuleResult:
    await bind_project(db, names.project_id)
    rule = await lock_rule_for_evaluation(db, names.project_id, rule_id)
    if rule is None:
        return RuleResult(RuleOutcome.SKIPPED)
    stored = await db.get(AlertState, rule.id, populate_existing=True)
    if judged_by_newer_pass(stored, rule, now):
        return RuleResult(RuleOutcome.SKIPPED)
    # Windows end on the minute, like the rule preview's, so both read the same windows.
    reading = await read_rule(db, rule, now.replace(second=0, microsecond=0), cache)
    if reading is None:
        return RuleResult(RuleOutcome.SKIPPED)
    if reading.budget is not None and fired_last_period(stored, reading.budget, now):
        # A new period ends the old one's alert whatever this pass reads (it may be unknown).
        # The resolve reports this period's spend when it is known, not the ended period's.
        await close_firing_alert(
            db, rule, now, settings=settings, reason="period_rollover", value=reading.value
        )
    transition: Transition | None = None
    state = rule_state(stored)
    recovering = state.clear_since  # no data (or no baseline yet) leaves the window as it is
    if reading.has_data:
        condition = rule_condition(rule, reading.threshold)
        transition = evaluate_rule(condition, reading.value, state, now)
        recovering = clear_since(condition, reading.value, state, now)
    await save_state(
        db,
        rule,
        stored,
        value=reading.value,
        now=now,
        to_state=None if transition is None else transition.to_state,
        clear_since=recovering,
    )
    if transition is not None:
        described = None
        if reading.budget is not None:
            described = budget_payload(reading.budget, transition.value, now)
        await _announce(db, rule, names, transition, now, settings, described)
    outcome = RuleOutcome.OK if reading.has_data else RuleOutcome.NO_DATA
    return RuleResult(outcome, transition, rule)


async def _announce(
    db: AsyncSession,
    rule: AlertRule,
    names: ProjectNames,
    transition: Transition,
    now: datetime,
    settings: "Settings",
    budget: BudgetPayload | None = None,
) -> None:
    """Write the event and, unless the rule is muted, queue its notifications. A budget rule's
    payload carries its `budget` description (budgets are never muted)."""
    payload = await apply_transition(
        db, rule, names, transition, app_base_url=settings.app_base_url, budget=budget
    )
    if payload is None:
        return
    if rule.muted_until is not None and rule.muted_until > now:
        logger.info("alert_notification_muted", rule_id=str(rule.id), alert_event=payload.event)
        return
    await notify_channels(db, rule.channel_ids, payload, settings=settings)


async def close_firing_alert(
    db: AsyncSession,
    rule: AlertRule,
    now: datetime,
    *,
    settings: "Settings | None" = None,
    reason: CloseReason = "edit",
    value: Decimal | None = None,
) -> bool:
    """Resolve the rule's alert now if it is firing, whatever its metric reads.

    For a rule disabled, deleted (call before the row goes) or redefined, and a budget rule still
    firing from a past period (`reason`, logged): the open event resolves at `now` (never before
    it started), the state becomes `ok` since `now`, and `alert.resolved` is queued unless muted.
    Its `value` is `value` when just measured, else the last one (`last_reading`); a budget's
    payload describes the period that value belongs to. Runs in the caller's transaction, which
    holds the rule's lock; never commits. Returns whether there was a firing alert to close.
    """
    stored = await db.get(AlertState, rule.id, populate_existing=True)
    if stored is None or stored.state is not RuleStateName.FIRING:
        return False
    event = await open_event(db, rule)
    measured, measured_at = last_reading(stored, event, now, value)
    stored.state = RuleStateName.OK
    stored.since = now
    stored.clear_since = None
    names = await project_names(db, rule.project_id)
    threshold = event.threshold if event is not None else None
    if names is None or event is None or measured is None or threshold is None:
        # Nothing complete enough to tell anyone; close the episode quietly.
        await resolve_open_event(db, rule, now, not_before_start=True)
        logger.warning("alert_closed_without_notification", rule_id=str(rule.id))
    else:
        transition = Transition(RuleStateName.OK, measured, threshold, now)
        budget = await budget_for_rule(db, rule) if rule.kind is AlertRuleKind.BUDGET else None
        described = None if budget is None else budget_payload(budget, measured, measured_at)
        await _announce(db, rule, names, transition, now, settings or get_settings(), described)
    await db.flush()
    # Counted before the caller commits; a failed commit over-counts one, which a counter allows.
    ALERT_TRANSITIONS.labels(kind=rule.kind.value, to_state=RuleStateName.OK.value).inc()
    logger.info(_CLOSED_EVENTS[reason], rule_id=str(rule.id), project_id=str(rule.project_id))
    return True

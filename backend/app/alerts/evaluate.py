"""Alert state machine. Pure: the caller loads the state and applies the transition.

One machine serves every rule kind. Threshold and budget rules pass their stored
threshold; anomaly rules compute theirs first (see `anomaly.py`).

A firing rule resolves only once its value has read clear in `RECOVERY_MINUTES` consecutive
minute passes, so a value hovering at the line does not fire and resolve every minute.
"""

import operator
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from app.alerts.types import Comparator, RuleStateName

_COMPARE: dict[Comparator, Callable[[Decimal, Decimal], bool]] = {
    Comparator.GT: operator.gt,
    Comparator.GTE: operator.ge,
    Comparator.LT: operator.lt,
    Comparator.LTE: operator.le,
}

# Passes run once a minute and read windows that end on the minute, so the window is counted in
# whole minutes: clear at 12:01, 12:02 and 12:03 resolves at 12:03, whatever the seconds.
RECOVERY_MINUTES = 3
_RECOVERY_SPAN = timedelta(minutes=RECOVERY_MINUTES - 1)  # first to last clear pass


@dataclass(frozen=True, slots=True)
class RuleCondition:
    """What the state machine needs from a rule."""

    comparator: Comparator
    threshold: Decimal
    cooldown_minutes: int


@dataclass(frozen=True, slots=True)
class RuleState:
    """Current state of a rule.

    `since` is when the rule entered `state`; for `ok` after a resolve that is
    the resolve time. A rule with no stored row is `RuleState(ok, None, None)`,
    which never blocks a fire. `clear_since` is when a firing rule's value first read clear
    in the current recovery window; None when it is not firing or its last known value breached.
    """

    state: RuleStateName
    since: datetime | None
    last_value: Decimal | None
    clear_since: datetime | None = None


NO_STATE = RuleState(RuleStateName.OK, None, None)


@dataclass(frozen=True, slots=True)
class Transition:
    to_state: RuleStateName
    value: Decimal
    threshold: Decimal
    at: datetime


def is_breach(comparator: Comparator, value: Decimal, threshold: Decimal) -> bool:
    return _COMPARE[comparator](value, threshold)


def evaluate_rule(
    rule: RuleCondition,
    value: Decimal | None,
    state: RuleState,
    now: datetime,
) -> Transition | None:
    """Return the state change for one evaluation, or `None` to stay put.

    An unknown value (`None`) never changes state. `ok -> firing` needs a breach
    and no resolve inside the cooldown; `firing -> ok` needs the value clear for the
    recovery window (`clear_since`).
    """
    if value is None:
        return None
    if state.state is RuleStateName.FIRING:
        start = clear_since(rule, value, state, now)
        if start is None or _minute(now) - _minute(start) < _RECOVERY_SPAN:
            return None
        return Transition(RuleStateName.OK, value, rule.threshold, now)
    if not is_breach(rule.comparator, value, rule.threshold) or _in_cooldown(
        state, rule.cooldown_minutes, now
    ):
        return None
    return Transition(RuleStateName.FIRING, value, rule.threshold, now)


def clear_since(
    rule: RuleCondition, value: Decimal | None, state: RuleState, now: datetime
) -> datetime | None:
    """The firing rule's recovery window start after this evaluation, to store with the state.

    A clear value starts the window (or keeps it), a breach ends it, and an unknown value leaves
    it as it was. A rule that is not firing has none.
    """
    if state.state is not RuleStateName.FIRING:
        return None
    if value is None:
        return state.clear_since
    if is_breach(rule.comparator, value, rule.threshold):
        return None
    return state.clear_since or now


def _minute(at: datetime) -> datetime:
    return at.replace(second=0, microsecond=0)


def _in_cooldown(state: RuleState, cooldown_minutes: int, now: datetime) -> bool:
    if state.since is None:
        return False
    return now - state.since < timedelta(minutes=cooldown_minutes)

"""From a stored rule to what the metric read and the state machine take. Pure: no I/O.

Shared by the rule preview and the evaluation job, so both read a rule the same way: the same
filters, the same trailing windows and the same baseline windows.
"""

import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from app.alerts.evaluate import NO_STATE, RuleCondition, RuleState
from app.alerts.types import AlertRuleKind, MetricFilters
from app.api.window import TimeWindow
from app.db.models import AlertRule, AlertState


def metric_filters(filters: Mapping[str, Any]) -> MetricFilters:
    """A rule's stored `filters` as the metric read's filters.

    People's rules use `environment`, `provider`, `model`, `error_class` and `rate_limit_kind`; a
    budget's rule may also hold its scope: `source_key_id`, `external_user_id`, `trace_name`, or
    the pair `metadata_key` and `metadata_value` (`app.budgets.rule_sync` writes both or neither;
    half a pair is ignored rather than read as a filter). Other keys are ignored.
    """
    source_key_id = filters.get("source_key_id")
    return MetricFilters(
        environment=_text(filters.get("environment")),
        provider=_text(filters.get("provider")),
        model=_text(filters.get("model")),
        source_key_id=uuid.UUID(str(source_key_id)) if source_key_id is not None else None,
        external_user_id=_text(filters.get("external_user_id")),
        metadata=_pair(filters.get("metadata_key"), filters.get("metadata_value")),
        trace_name=_text(filters.get("trace_name")),
        error_class=_text(filters.get("error_class")),
        rate_limit_kind=_text(filters.get("rate_limit_kind")),
    )


def _pair(key: Any, value: Any) -> tuple[str, str] | None:
    return None if key is None or value is None else (str(key), str(value))


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def rule_condition(rule: AlertRule, threshold: Decimal | None = None) -> RuleCondition:
    """What the state machine compares for `rule`.

    Threshold and budget rules use their stored threshold. An anomaly rule's threshold moves
    with its baseline, so the caller computes it (`anomaly_threshold`) and passes it here.
    """
    line = threshold if rule.kind is AlertRuleKind.ANOMALY else rule.threshold
    if line is None:
        raise ValueError(f"rule {rule.id} has no threshold to compare with")
    return RuleCondition(
        comparator=rule.comparator, threshold=line, cooldown_minutes=rule.cooldown_minutes
    )


def rule_state(state: AlertState | None) -> RuleState:
    """The stored state as the state machine's; a rule never evaluated is `ok` since never."""
    if state is None:
        return NO_STATE
    return RuleState(state=state.state, since=state.since, last_value=state.last_value)


def trailing_window(end: datetime, minutes: int) -> TimeWindow:
    """The `minutes` before `end`."""
    return TimeWindow(start=end - timedelta(minutes=minutes), end=end)


def previous_windows(window: TimeWindow, count: int) -> list[TimeWindow]:
    """The `count` windows of the same length right before `window`, oldest first.

    An anomaly rule's baseline is its metric over these.
    """
    length = window.length
    return [
        TimeWindow(start=window.start - length * index, end=window.start - length * (index - 1))
        for index in range(count, 0, -1)
    ]

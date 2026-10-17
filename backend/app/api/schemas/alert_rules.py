"""Alerts: a project's alert rules, their events and the rule preview.

The create and edit body, `RuleSpec`, and the preview body, `RuleDefinition`, are owned by the
alerts domain (`app.alerts.rule_spec`), which validates them; they are re-exported here so
routers keep importing from `app.api.schemas`. Metric values and thresholds are decimal strings
without an exponent; an unknown value is `null`, never `"0"`.

`filters` holds exact matches on `environment`, `provider` and `model`, and on metrics
`error_rate` and `error_calls` also `error_class` and `rate_limit_kind`, which narrow the failed
calls counted: `error_calls` is the number of failed llm calls in the window (0 when none), and
`error_rate` with either filter is the failed calls of that class and kind divided by all llm
calls. Either filter on another metric is a `422 VALIDATION_ERROR`
(`filters.error_class: applies to error_rate and error_calls`).
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict

from app.alerts.payload import DecimalStr
from app.alerts.rule_spec import RuleDefinition, RuleSpec
from app.alerts.types import AlertRuleKind, Comparator, Metric, RuleStateName
from app.api.schemas.common import ApiModel, UserOut

__all__ = [
    "AlertEventOut",
    "AlertRuleOut",
    "AlertRuleStateOut",
    "MuteRequest",
    "RuleDefinition",
    "RulePreviewOut",
    "RulePreviewPoint",
    "RuleSpec",
]


class AlertRuleStateOut(ApiModel):
    state: RuleStateName
    # When the rule entered `state`; for `ok` after a resolve, the resolve time.
    since: datetime | None
    # The metric at the last evaluation; null when it was unknown.
    last_value: DecimalStr | None
    last_evaluated_at: datetime | None


class AlertRuleOut(ApiModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    kind: AlertRuleKind
    metric: Metric
    comparator: Comparator
    # Null for an anomaly rule, whose line comes from its baseline.
    threshold: DecimalStr | None
    window_minutes: int
    # Anomaly rules only; null otherwise.
    baseline_windows: int | None
    sensitivity: DecimalStr | None
    filters: dict[str, Any]
    cooldown_minutes: int
    enabled: bool
    channel_ids: list[uuid.UUID]
    # While in the future the rule records events but notifies nobody.
    muted_until: datetime | None
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    # Null until the rule's first evaluation.
    state: AlertRuleStateOut | None = None


def _as_utc(moment: datetime) -> datetime:
    # A time without an offset is UTC, as for every other datetime the API accepts.
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


class MuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # In the future and at most 30 days ahead (checked against the server's clock); null
    # unmutes.
    until: Annotated[datetime, AfterValidator(_as_utc)] | None


class RulePreviewPoint(BaseModel):
    # The end of the window the value covers.
    window_end: datetime
    value: DecimalStr | None
    # The line at that point: the threshold, or the anomaly line (null without a baseline).
    threshold: DecimalStr | None


class RulePreviewOut(BaseModel):
    points: list[RulePreviewPoint]
    # True when some value is a percentile estimated from hourly histograms.
    approximate: bool


class AlertEventOut(BaseModel):
    id: uuid.UUID
    rule_id: uuid.UUID
    rule_name: str
    # `budget` for the event of a budget's rule.
    rule_kind: AlertRuleKind
    # `firing` while open, `resolved` once `resolved_at` is set.
    state: Literal["firing", "resolved"]
    # The value and the line that fired the event.
    value: DecimalStr | None
    threshold: DecimalStr | None
    started_at: datetime
    resolved_at: datetime | None
    acknowledged_by: UserOut | None
    acknowledged_at: datetime | None

"""`AlertPayload`: the one description of an alert event.

It is the JSON body a webhook receives, the PagerDuty `custom_details`, the basis of the Slack
and email messages, and what `alert_events.payload` stores. Decimals are written as normalised
decimal strings without an exponent (`"0.05"`, never `5E-2`) and datetimes as
`YYYY-MM-DDTHH:MM:SSZ`, so the same event always serialises to the same bytes.

`InsightPayload` describes an insight the Doctor opened (`insight.opened`); it travels through the
same channels and outbox as an alert. `ChannelTestPayload` is the much smaller body of a
channel's test-send. `parse_payload` tells the three apart by `event`.
"""

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    TypeAdapter,
    model_validator,
)

from app.alerts.types import AlertRuleKind, Comparator, Metric
from app.core.decimals import decimal_to_str

PAYLOAD_VERSION = "2026-10-01"


def datetime_to_str(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


DecimalStr = Annotated[Decimal, PlainSerializer(decimal_to_str, return_type=str)]
UtcDatetime = Annotated[AwareDatetime, PlainSerializer(datetime_to_str, return_type=str)]


class _Model(BaseModel):
    # Unknown keys are ignored on purpose: an outbox row's payload is this model's JSON plus a
    # `summary`, and a newer producer may add fields an older consumer does not know.
    model_config = ConfigDict(frozen=True)


class OrgRef(_Model):
    id: uuid.UUID
    name: str


class ProjectRef(_Model):
    id: uuid.UUID
    name: str


class RulePayload(_Model):
    id: uuid.UUID
    name: str
    kind: AlertRuleKind
    metric: Metric
    comparator: Comparator
    # The configured threshold; None for an anomaly rule, whose effective threshold moves.
    threshold: DecimalStr | None
    # None for a budget rule.
    window_minutes: int | None
    filters: dict[str, str]


class BudgetPayload(_Model):
    id: uuid.UUID
    name: str
    scope: Literal["project", "gateway_key", "user", "model", "metadata", "trace_name"]
    scope_id: str | None
    period: Literal["daily", "monthly"]
    amount_usd: DecimalStr
    spent_usd: DecimalStr
    action: Literal["notify", "block"]
    resets_at: UtcDatetime


class _EventPayload(_Model):
    """A payload a webhook signs: its JSON form and the exact bytes of that form."""

    def to_json_dict(self) -> dict[str, Any]:
        """The payload as plain JSON types, in field order."""
        return self.model_dump(mode="json")

    def canonical_json(self) -> bytes:
        """The exact bytes a webhook signs and sends: compact separators, UTF-8."""
        return json.dumps(self.to_json_dict(), separators=(",", ":"), ensure_ascii=False).encode()


class AlertPayload(_EventPayload):
    version: str = PAYLOAD_VERSION
    event: Literal["alert.fired", "alert.resolved", "budget.exceeded"]
    event_id: uuid.UUID
    occurred_at: UtcDatetime
    org: OrgRef
    project: ProjectRef
    rule: RulePayload
    value: DecimalStr
    # The effective threshold: the anomaly threshold for an anomaly rule.
    threshold: DecimalStr
    started_at: UtcDatetime
    resolved_at: UtcDatetime | None
    # Set for budget events, including the `alert.resolved` of a budget rule.
    budget: BudgetPayload | None
    url: str

    @model_validator(mode="after")
    def _event_has_its_fields(self) -> Self:
        if self.event == "budget.exceeded" and self.budget is None:
            raise ValueError("a budget.exceeded event carries the budget")
        if self.event == "alert.resolved" and self.resolved_at is None:
            raise ValueError("an alert.resolved event carries resolved_at")
        return self


class InsightWindow(_Model):
    start: UtcDatetime
    end: UtcDatetime


class InsightEvidence(_Model):
    # The most recent first, at most 20.
    trace_ids: list[str]
    # Decimals arrive as strings (as `insights.evidence` stores them); None is unknown.
    metrics: dict[str, str | int | None]
    window: InsightWindow


class InsightBody(_Model):
    id: uuid.UUID
    kind: str
    label: str
    severity: Literal["info", "warning", "critical"]
    # Names the problem within the project; stable across reopenings (PagerDuty's dedup key).
    fingerprint: str
    title: str
    summary: str
    failure_layer: str
    suggested_fix: str
    verification: str
    occurrences: int
    first_seen_at: UtcDatetime
    last_seen_at: UtcDatetime
    evidence: InsightEvidence


class InsightPayload(_EventPayload):
    version: str = PAYLOAD_VERSION
    event: Literal["insight.opened"] = "insight.opened"
    event_id: uuid.UUID
    occurred_at: UtcDatetime
    org: OrgRef
    project: ProjectRef
    insight: InsightBody
    url: str


# What `notify_channels` fans out to an organization's channels.
NotifyPayload = AlertPayload | InsightPayload


class ChannelTestPayload(_Model):
    """What a Slack, webhook or PagerDuty channel receives from its test-send."""

    version: str = PAYLOAD_VERSION
    event: Literal["test"] = "test"
    occurred_at: UtcDatetime
    org: OrgRef
    url: str

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


AnyPayload = Annotated[
    AlertPayload | InsightPayload | ChannelTestPayload, Field(discriminator="event")
]
_ANY_PAYLOAD: TypeAdapter[AlertPayload | InsightPayload | ChannelTestPayload] = TypeAdapter(
    AnyPayload
)


def parse_payload(raw: dict[str, Any]) -> AlertPayload | InsightPayload | ChannelTestPayload:
    """The payload of an outbox row. Raises `pydantic.ValidationError` when it is not one."""
    return _ANY_PAYLOAD.validate_python(raw)


def rule_url(
    app_base_url: str, org_id: uuid.UUID, project_id: uuid.UUID, rule_id: uuid.UUID
) -> str:
    return f"{app_base_url.rstrip('/')}/{org_id}/{project_id}/alerts/{rule_id}"


def budgets_url(app_base_url: str, org_id: uuid.UUID, project_id: uuid.UUID) -> str:
    return f"{app_base_url.rstrip('/')}/{org_id}/{project_id}/budgets"


def insight_url(
    app_base_url: str, org_id: uuid.UUID, project_id: uuid.UUID, insight_id: uuid.UUID
) -> str:
    return f"{app_base_url.rstrip('/')}/{org_id}/{project_id}/doctor/{insight_id}"

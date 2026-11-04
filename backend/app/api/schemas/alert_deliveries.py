"""Alerts: the delivery log of an alert channel.

A delivery is one outbox row: a Slack, webhook or PagerDuty post, or one email to one
recipient. The shape never carries the row's `target` (it holds addresses) or its payload;
`summary` is the part of the payload that describes the alert.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import ValidationError

from app.api.schemas.common import ApiModel
from app.notifications.queries import DeliveryRow


class DeliverySummaryOut(ApiModel):
    # `alert.fired`, `alert.resolved`, `budget.exceeded`, `insight.opened`, `weekly_digest`... or
    # `test` for a test send, which has no rule.
    event: str
    event_id: uuid.UUID | None = None
    # Set for an alert or budget event.
    rule_id: uuid.UUID | None = None
    rule_name: str | None = None
    # Set for `insight.opened`: the insight, its project (channels are shared by the organization's
    # projects) and its title when it was sent. Rows queued before `project_id` was kept lack it.
    project_id: uuid.UUID | None = None
    insight_id: uuid.UUID | None = None
    title: str | None = None


class DeliveryOut(ApiModel):
    id: uuid.UUID
    # What delivers it: `email`, `slack`, `webhook` or `pagerduty`.
    kind: str
    status: Literal["pending", "sent", "failed"]
    attempts: int
    # When the worker tries next; for a settled row, when it last was due.
    next_attempt_at: datetime
    # Why the last attempt failed. Never holds a URL, a routing key or an address.
    last_error: str | None
    created_at: datetime
    summary: DeliverySummaryOut | None
    # Whether `POST .../retry` would take it: a `failed` row with a message to send that recovery
    # has not quarantined. False for an email that could not be rendered.
    retryable: bool


def delivery_out(row: DeliveryRow) -> DeliveryOut:
    """The API shape. A stored summary that does not fit is shown as none rather than failing."""
    summary: DeliverySummaryOut | None = None
    if row.summary is not None:
        try:
            summary = DeliverySummaryOut.model_validate(row.summary)
        except ValidationError:
            summary = None
    return DeliveryOut(
        id=row.id,
        kind=row.kind,
        status=row.status.value,
        attempts=row.attempts,
        next_attempt_at=row.next_attempt_at,
        last_error=row.last_error,
        created_at=row.created_at,
        summary=summary,
        retryable=row.retryable,
    )

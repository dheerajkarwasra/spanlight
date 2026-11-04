"""Reads of the outbox for the delivery log of an alert channel.

`notification_outbox` is not under row-level security and carries no organization column, so
every query here ties the rows to the organization through `alert_channels`: the `org_id` and
`channel_id` filters are what keep one organization from seeing another's deliveries. Nothing
here returns `target` (it holds email addresses) or the payload beyond its `summary`.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, Select, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AlertChannel, NotificationOutbox, NotificationStatus
from app.notifications.outbox import retryable


@dataclass(frozen=True)
class DeliveryRow:
    """One outbox row as the delivery log shows it."""

    id: uuid.UUID
    kind: str
    status: NotificationStatus
    attempts: int
    next_attempt_at: datetime
    last_error: str | None
    created_at: datetime
    # The payload's `summary` key, which settled rows keep; None for a row without one.
    summary: dict[str, Any] | None
    # Whether `retry_failed` would take it (`outbox.retryable`).
    retryable: bool


def _channel_deliveries(org_id: uuid.UUID, channel_id: uuid.UUID) -> tuple[Any, ...]:
    """The conditions that tie an outbox row to a channel of the organization."""
    channel_of_org = (
        select(AlertChannel.id)
        .where(AlertChannel.org_id == org_id, AlertChannel.id == channel_id)
        .scalar_subquery()
    )
    return (NotificationOutbox.channel_id == channel_of_org,)


def _delivery_select() -> Select[Any]:
    # Only the payload's `summary`: the rest of a kept payload is a rendered message.
    return select(
        NotificationOutbox.id,
        NotificationOutbox.kind,
        NotificationOutbox.status,
        NotificationOutbox.attempts,
        NotificationOutbox.next_attempt_at,
        NotificationOutbox.last_error,
        NotificationOutbox.created_at,
        NotificationOutbox.payload["summary"].label("summary"),
        retryable().label("retryable"),
    )


def _delivery_row(row: Row[Any]) -> DeliveryRow:
    return DeliveryRow(
        id=row.id,
        kind=row.kind,
        status=row.status,
        attempts=row.attempts,
        next_attempt_at=row.next_attempt_at,
        last_error=row.last_error,
        created_at=row.created_at,
        summary=row.summary if isinstance(row.summary, dict) else None,
        retryable=bool(row.retryable),
    )


async def list_deliveries(
    db: AsyncSession,
    org_id: uuid.UUID,
    channel_id: uuid.UUID,
    *,
    status: NotificationStatus | None,
    after: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> list[DeliveryRow]:
    """Up to `limit + 1` rows of the channel, newest first (`created_at desc, id desc`).

    The extra row tells the caller there is a next page. `after` is the sort key of the last row
    of the previous page.
    """
    statement = (
        _delivery_select()
        .where(*_channel_deliveries(org_id, channel_id))
        .order_by(NotificationOutbox.created_at.desc(), NotificationOutbox.id.desc())
        .limit(limit + 1)
    )
    if status is not None:
        statement = statement.where(NotificationOutbox.status == status)
    if after is not None:
        # A row comparison, in the index's `(created_at DESC, id DESC)` order, is an index
        # condition; the equivalent OR of two comparisons would only be a filter.
        statement = statement.where(
            tuple_(NotificationOutbox.created_at, NotificationOutbox.id) < tuple_(*after)
        )
    return [_delivery_row(row) for row in await db.execute(statement)]


async def get_delivery(
    db: AsyncSession, org_id: uuid.UUID, channel_id: uuid.UUID, delivery_id: uuid.UUID
) -> DeliveryRow | None:
    """One delivery of the channel, or None when it is not the channel's (or the org's)."""
    row = (
        await db.execute(
            _delivery_select().where(
                *_channel_deliveries(org_id, channel_id), NotificationOutbox.id == delivery_id
            )
        )
    ).first()
    return None if row is None else _delivery_row(row)


async def get_email_recipient(
    db: AsyncSession, org_id: uuid.UUID, channel_id: uuid.UUID, delivery_id: uuid.UUID
) -> str | None:
    """The address an `email` delivery of the channel goes to; None for any other kind.

    For the caller's checks only: it must never be returned or logged.
    """
    recipient = await db.scalar(
        select(NotificationOutbox.target["to"].as_string()).where(
            *_channel_deliveries(org_id, channel_id),
            NotificationOutbox.id == delivery_id,
            NotificationOutbox.kind == "email",
        )
    )
    return recipient

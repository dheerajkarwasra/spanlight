"""An alert channel's delivery log: reading it and retrying a delivery that failed. Callers commit.

The log lists the channel's outbox rows (`app.notifications.queries`), email recipients and test
sends included. A retry is a write under the organization, so it takes the organization's write
lock first, like every channel write (`app.alerts.service`), then writes the audit event. The
audit metadata holds the channel's name and kind and the delivery's id, never an address.
"""

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import queries
from app.alerts.channels.errors import ChannelNotFoundError
from app.alerts.service import allowed_recipients
from app.config import Settings
from app.core.pagination import decode_uuid_cursor, encode_cursor
from app.db.models import AuditAction, NotificationStatus
from app.notifications import outbox
from app.notifications import queries as delivery_queries
from app.notifications.queries import DeliveryRow
from app.services.audit import record_audit
from app.services.deletion import lock_org_for_write


class DeliveryNotFoundError(Exception):
    """No delivery with this id on this channel."""


NotRetryableReason = Literal["not_failed", "never_rendered", "quarantined", "recipient_left"]


class DeliveryNotRetryableError(Exception):
    """The delivery cannot go back in the queue; `reason` says why (`outbox.retryable`)."""

    def __init__(self, reason: NotRetryableReason) -> None:
        super().__init__(reason)
        self.reason: NotRetryableReason = reason


async def list_deliveries(
    db: AsyncSession,
    org_id: uuid.UUID,
    channel_id: uuid.UUID,
    *,
    status: NotificationStatus | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[DeliveryRow], str | None]:
    """One page of the channel's deliveries, newest first, and the cursor for the next page.

    Raises `ChannelNotFoundError` when the channel is not the organization's.
    """
    if await queries.get_channel(db, org_id, channel_id) is None:
        raise ChannelNotFoundError
    rows = await delivery_queries.list_deliveries(
        db,
        org_id,
        channel_id,
        status=status,
        after=decode_uuid_cursor(cursor) if cursor else None,
        limit=limit,
    )
    page = rows[:limit]
    next_cursor = (
        encode_cursor(page[-1].created_at, str(page[-1].id)) if len(rows) > limit else None
    )
    return page, next_cursor


async def retry_delivery(
    db: AsyncSession,
    org_id: uuid.UUID,
    channel_id: uuid.UUID,
    delivery_id: uuid.UUID,
    actor_id: uuid.UUID,
    *,
    settings: Settings,
    now: datetime,
    ip: str | None = None,
) -> DeliveryRow:
    """Queue a failed delivery again and return it as it now stands.

    Raises `ChannelNotFoundError` (no such channel in the organization), `DeliveryNotFoundError`
    (no such delivery on the channel) and `DeliveryNotRetryableError` (it did not fail, it was
    never rendered, recovery quarantined it, or an email's recipient is no longer a verified
    member: the fan-out check runs again).
    """
    if not await lock_org_for_write(db, org_id):
        raise ChannelNotFoundError
    channel = await queries.get_channel(db, org_id, channel_id)
    if channel is None:
        raise ChannelNotFoundError
    row = await delivery_queries.get_delivery(db, org_id, channel_id, delivery_id)
    if row is None:
        raise DeliveryNotFoundError
    await _require_recipient(db, org_id, channel_id, delivery_id, settings=settings)
    if not await outbox.retry_failed(db, org_id, channel_id, delivery_id, now):
        raise DeliveryNotRetryableError(_why_not_retryable(row))
    await record_audit(
        db,
        org_id=org_id,
        actor_user_id=actor_id,
        action=AuditAction.ALERT_CHANNEL_RETRY_DELIVERY,
        target_type="alert_channel",
        target_id=channel.id,
        ip=ip,
        metadata={
            "name": channel.name,
            "kind": channel.kind.value,
            "delivery_id": str(delivery_id),
        },
    )
    retried = await delivery_queries.get_delivery(db, org_id, channel_id, delivery_id)
    if retried is None:  # pragma: no cover - the row was just updated under the org lock
        raise DeliveryNotFoundError
    return retried


def _why_not_retryable(row: DeliveryRow) -> NotRetryableReason:
    """The one condition of `outbox.retryable` the row fails, in the order it checks them."""
    if row.status is not NotificationStatus.FAILED:
        return "not_failed"
    if row.last_error is not None and row.last_error.startswith(outbox.RECOVERY_ERROR_PREFIX):
        return "quarantined"
    return "never_rendered"  # an email row with no `text`: it was never rendered


async def _require_recipient(
    db: AsyncSession,
    org_id: uuid.UUID,
    channel_id: uuid.UUID,
    delivery_id: uuid.UUID,
    *,
    settings: Settings,
) -> None:
    """An email goes only to a verified member, now as when it was queued."""
    recipient = await delivery_queries.get_email_recipient(db, org_id, channel_id, delivery_id)
    if recipient is None:
        return
    if not await allowed_recipients(db, org_id, [recipient.lower()], settings=settings):
        raise DeliveryNotRetryableError("recipient_left")

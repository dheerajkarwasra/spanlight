"""The producer end of the notification outbox table.

Producers call `enqueue` inside the transaction that makes the decision to notify, so the
decision and its delivery request commit or roll back together. Delivery claims rows under a
lease (see `lease.py`) and sends them (see `delivery.py`).
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.db.models import AlertChannel, NotificationOutbox, NotificationStatus

# `last_error` of a row quarantined by `spanlight restore-reconcile` (see `app.backups.recovery`).
RECOVERY_ERROR_PREFIX = "recovery:"


class NotificationKind(enum.StrEnum):
    """What delivers a notification. Alert emails use `email`, like every other mail."""

    EMAIL = "email"
    SLACK = "slack"
    WEBHOOK = "webhook"
    PAGERDUTY = "pagerduty"


async def enqueue(
    db: AsyncSession,
    kind: NotificationKind,
    target: dict[str, Any],
    payload: dict[str, Any],
    channel_id: uuid.UUID | None = None,
    *,
    failed: str | None = None,
) -> uuid.UUID:
    """Add a pending notification to the caller's transaction and return its id.

    This flushes but never commits: if the caller rolls back, the notification disappears
    with the rest of its work. `target` says where it goes, `payload` what it says. Once the
    notification is settled, the stored payload is reduced to its `subject` and `summary`, so a
    link in the body does not stay in the database (see `lease.py` for the exact rule).

    `failed` writes the row already failed after one attempt, with that `last_error`, for a
    notification known never to be sendable (an alert email that could not be rendered): the
    delivery log shows it and the worker never claims it. It must be safe to store and show.
    """
    row_id = new_id()
    settled: dict[str, Any] = {}
    if failed is not None:
        settled = {"status": NotificationStatus.FAILED, "attempts": 1, "last_error": failed}
    db.add(
        NotificationOutbox(
            id=row_id,
            kind=kind.value,
            channel_id=channel_id,
            target=target,
            payload=payload,
            **settled,
        )
    )
    await db.flush()
    return row_id


async def count_pending(db: AsyncSession) -> int:
    statement = (
        select(func.count())
        .select_from(NotificationOutbox)
        .where(NotificationOutbox.status == NotificationStatus.PENDING)
    )
    return int(await db.scalar(statement) or 0)


def retryable() -> ColumnElement[bool]:
    """Whether a channel row can go back in the queue: `failed`, with a message to send (an
    email that was never rendered has no `text`), and not quarantined by recovery. The
    delivery log shows this as `retryable`, and `retry_failed` requires it."""
    return and_(
        NotificationOutbox.status == NotificationStatus.FAILED,
        or_(
            NotificationOutbox.kind != NotificationKind.EMAIL.value,
            NotificationOutbox.payload.has_key("text"),
        ),
        or_(
            NotificationOutbox.last_error.is_(None),
            ~NotificationOutbox.last_error.startswith(RECOVERY_ERROR_PREFIX),
        ),
    )


async def retry_failed(
    db: AsyncSession, org_id: uuid.UUID, channel_id: uuid.UUID, row_id: uuid.UUID, now: datetime
) -> bool:
    """Put a failed alert delivery back in the queue; False when the row is not one.

    Only a `failed` row of this channel of this organization qualifies. A failed row that has a
    `channel_id` kept its payload (see `lease.py`), so it can be sent again, except an email
    that never had a message (`text`) because it could not be rendered; transactional mail
    has no channel and is never matched. The row keeps its id, so a receiver still sees the same
    `X-Spanlight-Delivery`. It starts over (`attempts = 0`, due now, no lease) and keeps its
    `last_error` until the next attempt replaces it. Flushes but never commits.

    A row whose `last_error` starts with `recovery:` was quarantined by `spanlight
    restore-reconcile` and is never retried here: only an explicit `authorize_replay` decision
    there puts it back in the queue (see `app.backups.recovery`).

    The `org_id` and `channel_id` conditions are the only other guard: the outbox is not under
    row-level security.
    """
    channel_of_org = (
        select(AlertChannel.id)
        .where(AlertChannel.org_id == org_id, AlertChannel.id == channel_id)
        .scalar_subquery()
    )
    retried = await db.scalar(
        update(NotificationOutbox)
        .where(
            NotificationOutbox.id == row_id,
            NotificationOutbox.channel_id == channel_of_org,
            retryable(),
        )
        .values(
            status=NotificationStatus.PENDING,
            attempts=0,
            next_attempt_at=now,
            lease_until=None,
        )
        .returning(NotificationOutbox.id)
    )
    return retried is not None

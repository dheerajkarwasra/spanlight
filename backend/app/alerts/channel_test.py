"""Send a test notification through a channel and report how it went (`POST .../test`).

The test goes through the outbox like a real alert: the rows are committed, then delivered in
this process with `deliver_now`, so the answer says whether the channel works. A row that fails
in a way a retry might fix stays pending for the worker. An email channel gets one row per
recipient who is still a verified member (a departed one is skipped and logged), and the answer
reports the first failure. A test that goes through records the time as the channel's
`verified_at`.

Each channel may be tested 10 times an hour (`CHANNEL_TEST_LIMITER`, keyed `alert-test:<id>`).
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts import queries
from app.alerts.channels.email_render import render_test_email
from app.alerts.channels.errors import (
    ChannelNotFoundError,
    EmailNotConfiguredError,
    RecipientNotMemberError,
)
from app.alerts.payload import ChannelTestPayload, OrgRef
from app.alerts.service import allowed_recipients
from app.config import Settings
from app.core.idempotency_store import StoredResponse, complete_operation
from app.core.ratelimit import PostgresTokenBucket
from app.db.models import AlertChannel, AlertChannelKind, AuditAction, Organization
from app.notifications.delivery import DeliveryOutcome, DeliveryResult, deliver_now
from app.notifications.outbox import NotificationKind, enqueue
from app.services.audit import record_audit
from app.services.deletion import lock_org_for_write

logger = structlog.get_logger(__name__)

TEST_SUMMARY: dict[str, Any] = {"event": "test"}
CHANNEL_TEST_LIMITER = PostgresTokenBucket(rate=10 / 3600, burst=10)
"""Per channel: 10 test notifications, refilling at 10 an hour."""

ChannelTestStatus = Literal["sent", "failed", "pending"]
SessionFactory = async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class ChannelTestResult:
    delivery_id: uuid.UUID
    status: ChannelTestStatus
    # The row's `last_error` for `failed` and a retryable failure; None otherwise.
    error: str | None


def limiter_key(channel_id: uuid.UUID) -> str:
    return f"alert-test:{channel_id}"


def build_test_payload(org: Organization, *, now: datetime, app_base_url: str) -> dict[str, Any]:
    """The body a Slack, webhook or PagerDuty test carries, plus the delivery log's summary."""
    body = ChannelTestPayload(
        occurred_at=now,
        org=OrgRef(id=org.id, name=org.name),
        url=f"{app_base_url.rstrip('/')}/",
    )
    return {**body.to_json_dict(), "summary": TEST_SUMMARY}


async def send_test(
    db: AsyncSession,
    session_factory: SessionFactory,
    org: Organization,
    channel_id: uuid.UUID,
    actor_id: uuid.UUID,
    *,
    settings: Settings,
    clock: Callable[[], datetime],
    ip: str | None = None,
) -> ChannelTestResult:
    """Queue, commit and deliver a test through the channel. Commits `db`, unlike the service.

    The rows must be committed before they can be claimed, so this is the one channel write
    that commits itself. Raises `ChannelNotFoundError`, `EmailNotConfiguredError` (an email
    channel while no email provider is configured) and `RecipientNotMemberError` (no recipient
    of an email channel is still a verified member), all before anything is queued.

    `verified_at` is only set when the channel is unchanged since the test was queued: an edit
    in between means the test reached a target the channel no longer has.
    """
    row_ids, seen_updated_at = await _enqueue_test(
        db, org, channel_id, actor_id, settings=settings, now=clock(), ip=ip
    )
    await complete_operation(
        db,
        StoredResponse(
            200,
            {"delivery_id": str(row_ids[0]), "status": "pending", "error": None},
            "application/json",
        ),
    )
    await db.commit()
    result = await _deliver(session_factory, row_ids)
    if result.status == "sent":
        await queries.mark_verified(db, org.id, channel_id, clock(), seen_updated_at)
    await complete_operation(
        db,
        StoredResponse(
            200,
            {
                "delivery_id": str(result.delivery_id),
                "status": result.status,
                "error": result.error,
            },
            "application/json",
        ),
        replace=True,
    )
    await db.commit()
    logger.info(
        "alert_channel_tested",
        org_id=str(org.id),
        channel_id=str(channel_id),
        deliveries=len(row_ids),
        status=result.status,
    )
    return result


async def _enqueue_test(
    db: AsyncSession,
    org: Organization,
    channel_id: uuid.UUID,
    actor_id: uuid.UUID,
    *,
    settings: Settings,
    now: datetime,
    ip: str | None,
) -> tuple[list[uuid.UUID], datetime]:
    """Queue the test's rows; returns them with the channel's `updated_at` as it was seen."""
    if not await lock_org_for_write(db, org.id):
        raise ChannelNotFoundError
    channel = await queries.lock_channel(db, org.id, channel_id)
    if channel is None:
        raise ChannelNotFoundError
    if channel.kind is AlertChannelKind.EMAIL and not settings.is_email_configured:
        raise EmailNotConfiguredError
    if channel.kind is AlertChannelKind.EMAIL:
        row_ids = await _enqueue_emails(db, org, channel, settings=settings)
    else:
        row_ids = [
            await enqueue(
                db,
                NotificationKind(channel.kind.value),
                {"channel_id": str(channel.id)},
                build_test_payload(org, now=now, app_base_url=settings.app_base_url),
                channel_id=channel.id,
            )
        ]
    await record_audit(
        db,
        org_id=org.id,
        actor_user_id=actor_id,
        action=AuditAction.ALERT_CHANNEL_TEST,
        target_type="alert_channel",
        target_id=channel.id,
        ip=ip,
        metadata={"name": channel.name, "kind": channel.kind.value, "deliveries": len(row_ids)},
    )
    return row_ids, channel.updated_at


async def _enqueue_emails(
    db: AsyncSession, org: Organization, channel: AlertChannel, *, settings: Settings
) -> list[uuid.UUID]:
    """One `email` row per recipient who may still receive the channel's mail."""
    addresses = [str(address) for address in channel.config.get("to", [])]
    recipients = await allowed_recipients(db, org.id, addresses, settings=settings)
    if len(recipients) < len(addresses):
        logger.warning(
            "alert_recipient_skipped",
            channel_id=str(channel.id),
            skipped=len(addresses) - len(recipients),
        )
    if not recipients:
        raise RecipientNotMemberError(addresses)
    message = render_test_email(channel.name, org.name)
    payload = {
        "subject": message.subject,
        "text": message.text,
        "html": message.html,
        "summary": TEST_SUMMARY,
    }
    return [
        await enqueue(db, NotificationKind.EMAIL, {"to": address}, payload, channel_id=channel.id)
        for address in recipients
    ]


async def _deliver(session_factory: SessionFactory, row_ids: list[uuid.UUID]) -> ChannelTestResult:
    """Deliver every row in turn; the result of the first one that did not go out, else sent."""
    results = [_as_result(row_id, await deliver_now(session_factory, row_id)) for row_id in row_ids]
    return next((result for result in results if result.status != "sent"), results[0])


def _as_result(row_id: uuid.UUID, result: DeliveryResult | None) -> ChannelTestResult:
    # None: a worker claimed the row first, and it is the worker's to deliver now.
    if result is None or result.status in (DeliveryOutcome.RETRY, DeliveryOutcome.LOST):
        return ChannelTestResult(row_id, "pending", result.error if result else None)
    if result.status is DeliveryOutcome.SENT:
        return ChannelTestResult(row_id, "sent", None)
    return ChannelTestResult(row_id, "failed", result.error)

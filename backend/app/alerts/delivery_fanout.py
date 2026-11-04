"""Turn one alert or insight payload into outbox rows for a channel, and queue them.

`outbox_rows_for` says which rows a channel gets. An email channel gets one `email` row per
allowed recipient, carrying the rendered message. An email that cannot be rendered still gets
its rows, written already `failed` with `EMAIL_RENDER_FAILED` and only the `summary`, so the
channel's delivery log says why no email came; it is logged and counted as `failed` once, never
sent and never retryable (a retry would render the same payload the same way, and raising would
roll back the event and every other channel's rows). Every other kind gets one
row whose target is `{"channel_id"}` and whose payload is the alert or insight payload (its
deliverer renders it and opens the channel's secret at send time). Every row's payload carries the
`summary` the delivery log shows after the row is settled, and every row is tied to its channel.

`enqueue_for_channel` is the async shell that evaluation and the channel code call inside their
transaction. It never commits.
"""

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.channels.email_render import render_alert_email, render_insight_email
from app.alerts.channels.resolve import ResolvedChannel
from app.alerts.payload import AlertPayload, InsightPayload, NotifyPayload
from app.alerts.service import allowed_recipients
from app.core.observability import NOTIFICATIONS_DELIVERED
from app.db.models import AlertChannel, AlertChannelKind
from app.email.message import EmailMessage
from app.notifications.delivery import DeliveryOutcome
from app.notifications.outbox import NotificationKind, enqueue

if TYPE_CHECKING:
    from app.config import Settings

logger = structlog.get_logger(__name__)

EMAIL_RENDER_FAILED = "The alert could not be rendered as an email"


@dataclass(frozen=True, slots=True)
class OutboxRowSpec:
    kind: NotificationKind
    target: dict[str, Any]
    payload: dict[str, Any]
    channel_id: uuid.UUID
    # Set when the row is written already failed, with this as its `last_error`.
    failed: str | None = None


def alert_summary(payload: AlertPayload) -> dict[str, Any]:
    """What the delivery log can still say about a row once its payload is reduced."""
    return {
        "event": payload.event,
        "event_id": str(payload.event_id),
        "rule_id": str(payload.rule.id),
        "rule_name": payload.rule.name,
    }


def insight_summary(payload: InsightPayload) -> dict[str, Any]:
    """The delivery log's description of an insight row."""
    return {
        "event": payload.event,
        "event_id": str(payload.event_id),
        "project_id": str(payload.project.id),
        "insight_id": str(payload.insight.id),
        "title": payload.insight.title,
    }


def _summary(payload: NotifyPayload) -> dict[str, Any]:
    if isinstance(payload, InsightPayload):
        return insight_summary(payload)
    return alert_summary(payload)


def _render_email(payload: NotifyPayload, *, channel_name: str) -> EmailMessage:
    if isinstance(payload, InsightPayload):
        return render_insight_email(payload, channel_name=channel_name)
    return render_alert_email(payload, channel_name=channel_name)


def _recipients(channel: ResolvedChannel) -> list[str]:
    return [str(address) for address in channel.config.get("to", [])]


def skipped_recipients(
    channel: ResolvedChannel, allowed_emails: frozenset[str] | None
) -> list[str]:
    """The email channel's addresses that `outbox_rows_for` leaves out (for the caller to log)."""
    if channel.kind is not AlertChannelKind.EMAIL or allowed_emails is None:
        return []
    return [address for address in _recipients(channel) if address.lower() not in allowed_emails]


def outbox_rows_for(
    channel: ResolvedChannel,
    payload: NotifyPayload,
    *,
    allowed_emails: frozenset[str] | None,
) -> list[OutboxRowSpec]:
    """The rows to queue for `channel`.

    `allowed_emails` is the lower-cased set of addresses that may receive mail (the verified
    members), or None when any address may (`ALERT_EMAIL_ANY_RECIPIENT`). Recipients outside it
    get no row; `skipped_recipients` lists them.
    """
    summary = _summary(payload)
    if channel.kind is AlertChannelKind.EMAIL:
        recipients = [
            address
            for address in _recipients(channel)
            if allowed_emails is None or address.lower() in allowed_emails
        ]
        body: dict[str, Any] = {"summary": summary}
        failed: str | None = None
        try:
            message = _render_email(payload, channel_name=channel.name)
        except Exception as exc:  # permanent: see the module docstring
            failed = EMAIL_RENDER_FAILED
            NOTIFICATIONS_DELIVERED.labels(
                NotificationKind.EMAIL.value, DeliveryOutcome.FAILED.value
            ).inc(len(recipients))
            logger.error(
                "alert_render_failed",
                channel_id=str(channel.id),
                alert_event=summary["event"],
                event_id=summary["event_id"],
                recipients=len(recipients),
                error_type=type(exc).__name__,
                exc_info=exc,
            )
        else:
            body |= {"subject": message.subject, "text": message.text, "html": message.html}
        return [
            OutboxRowSpec(NotificationKind.EMAIL, {"to": address}, body, channel.id, failed)
            for address in recipients
        ]
    return [
        OutboxRowSpec(
            NotificationKind(channel.kind.value),
            {"channel_id": str(channel.id)},
            {**payload.to_json_dict(), "summary": summary},
            channel.id,
        )
    ]


async def enqueue_for_channel(
    db: AsyncSession, channel: AlertChannel, payload: NotifyPayload, *, settings: "Settings"
) -> int:
    """Queue `payload` for `channel` in the caller's transaction; returns how many rows.

    An email channel only gets the recipients who are still verified members of the
    organization (all of them with `ALERT_EMAIL_ANY_RECIPIENT`); a departed member is skipped
    and logged by count.
    """
    allowed: frozenset[str] | None = None
    if channel.kind is AlertChannelKind.EMAIL and not settings.alert_email_any_recipient:
        addresses = [str(address) for address in channel.config.get("to", [])]
        allowed = frozenset(
            address.lower()
            for address in await allowed_recipients(
                db, channel.org_id, addresses, settings=settings
            )
        )
    resolved = ResolvedChannel(
        id=channel.id,
        org_id=channel.org_id,
        kind=channel.kind,
        name=channel.name,
        config=dict(channel.config),
        secret=None,  # rows never carry the secret; the deliverer opens it at send time
    )
    skipped = skipped_recipients(resolved, allowed)
    if skipped:
        logger.warning("alert_recipient_skipped", channel_id=str(channel.id), skipped=len(skipped))
    rows = outbox_rows_for(resolved, payload, allowed_emails=allowed)
    for row in rows:
        await enqueue(
            db, row.kind, row.target, row.payload, channel_id=row.channel_id, failed=row.failed
        )
    return len(rows)

"""Deliver an alert or an insight to a customer's endpoint as a signed JSON POST.

The outbox row's target is `{"channel_id", "delivery_id"}`. The body is the alert (or insight)
payload's canonical JSON (the stored payload without its `summary`), exactly the bytes that are
signed, so a receiver can verify the signature over what it read. Headers: `X-Spanlight-Event`,
`X-Spanlight-Delivery` (the outbox row's id, constant across retries, for deduplication),
`X-Spanlight-Timestamp` (unix seconds, fresh on every attempt) and `X-Spanlight-Signature`.

Any 2xx is success; 408, 429, 5xx, timeouts and connection errors are retried; other 4xx and
every 3xx (redirects are never followed) are permanent. The URL and the signing secret never
appear in an error.
"""

import json
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.channels.http import SharedClient, post_checked
from app.alerts.channels.target import BAD_PAYLOAD, BAD_TARGET, resolve_target, summary_event
from app.alerts.payload import AlertPayload, InsightPayload, parse_payload
from app.db.models import AlertChannelKind
from app.notifications.registry import PermanentDeliveryError
from app.notifications.signing import sign

if TYPE_CHECKING:
    from app.config import Settings

NO_URL = "The webhook channel has no URL"
NO_SECRET = "The webhook channel has no signing secret"  # noqa: S105 - a message, not a secret

logger = structlog.get_logger(__name__)


def webhook_body(payload: dict[str, Any]) -> tuple[bytes, str]:
    """The bytes to send and the event name, from an outbox row's payload.

    Raises `pydantic.ValidationError` when the payload is not an alert, insight or test event.
    """
    parsed = parse_payload(payload)
    if isinstance(parsed, AlertPayload | InsightPayload):
        return parsed.canonical_json(), parsed.event
    document = json.dumps(parsed.to_json_dict(), separators=(",", ":"), ensure_ascii=False)
    return document.encode(), parsed.event


class WebhookDeliverer:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: "Settings",
        client: SharedClient,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._client = client
        self._clock = clock

    async def deliver(self, target: dict[str, Any], payload: dict[str, Any]) -> None:
        delivery_id = target.get("delivery_id")
        if not delivery_id:
            raise PermanentDeliveryError(BAD_TARGET)
        channel = await resolve_target(
            self._session_factory,
            self._settings,
            target,
            kind=AlertChannelKind.WEBHOOK,
            label="webhook",
        )
        url = channel.config.get("url")
        if not isinstance(url, str) or not url:
            raise PermanentDeliveryError(NO_URL)
        if channel.secret is None:
            raise PermanentDeliveryError(NO_SECRET)
        try:
            body, event = webhook_body(payload)
        except ValidationError:
            logger.warning("alert_payload_unparseable", alert_event=summary_event(payload))
            raise PermanentDeliveryError(BAD_PAYLOAD) from None
        timestamp = int(self._clock())
        headers = {
            "x-spanlight-event": event,
            "x-spanlight-delivery": str(delivery_id),
            "x-spanlight-timestamp": str(timestamp),
            "x-spanlight-signature": sign(channel.secret.encode(), timestamp, body),
        }
        await post_checked(self._client.get(), url, body=body, headers=headers)

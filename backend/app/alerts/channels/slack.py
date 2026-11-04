"""Deliver an alert to a Slack channel through an incoming-webhook URL.

The outbox row's target is `{"channel_id"}`. The URL is the channel's sealed secret, opened just
before sending (`resolve_channel`), and it only reaches the HTTP client: nothing here logs it or
puts it in an error message. Slack answers 2xx on success; 429 and 5xx are retried; Slack's other
refusals (404 `no_service`, 403 `invalid_token`, 400) cannot be fixed by a retry.
"""

import json
import uuid
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.channels.http import SharedClient, post_checked
from app.alerts.channels.resolve import resolve_channel
from app.alerts.channels.slack_format import format_slack
from app.alerts.payload import parse_payload
from app.db.models import AlertChannelKind
from app.notifications.registry import PermanentDeliveryError

if TYPE_CHECKING:
    from app.config import Settings

CHANNEL_GONE = "The Slack channel was deleted"
NOT_A_SLACK_CHANNEL = "The channel is not a Slack channel"
NO_URL = "The Slack channel has no webhook URL"
BAD_TARGET = "The delivery names no valid channel"
BAD_PAYLOAD = "The delivery carries no valid alert payload"
RENDER_FAILED = "The alert could not be rendered as a Slack message"
logger = structlog.get_logger(__name__)


class SlackDeliverer:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: "Settings",
        client: SharedClient,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._client = client

    async def deliver(self, target: dict[str, Any], payload: dict[str, Any]) -> None:
        url = await self._url(target)
        try:
            parsed = parse_payload(payload)
        except ValidationError:
            raise PermanentDeliveryError(BAD_PAYLOAD) from None
        try:
            message = format_slack(parsed)
        except Exception as exc:  # a retry renders the same payload the same way
            logger.error(
                "alert_render_failed",
                channel_id=str(target.get("channel_id")),
                alert_event=parsed.event,
                error_type=type(exc).__name__,
                exc_info=exc,
            )
            raise PermanentDeliveryError(RENDER_FAILED) from None
        body = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode()
        await post_checked(self._client.get(), url, body=body)

    async def _url(self, target: dict[str, Any]) -> str:
        """The channel's webhook URL, or `PermanentDeliveryError` when it has none to use."""
        try:
            channel_id = uuid.UUID(str(target["channel_id"]))
        except (KeyError, ValueError):
            raise PermanentDeliveryError(BAD_TARGET) from None
        channel = await resolve_channel(self._session_factory, channel_id, settings=self._settings)
        if channel is None:
            raise PermanentDeliveryError(CHANNEL_GONE)
        if channel.kind is not AlertChannelKind.SLACK:
            raise PermanentDeliveryError(NOT_A_SLACK_CHANNEL)
        if not channel.secret:
            raise PermanentDeliveryError(NO_URL)
        return channel.secret

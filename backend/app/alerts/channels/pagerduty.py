"""Deliver an alert to PagerDuty as an Events API v2 event.

The outbox row's target is `{"channel_id"}`; the channel's sealed secret is the routing key. A
firing alert (and a budget breach) sends `trigger`, a resolved one `resolve`; both carry
`dedup_key = spanlight-rule-<rule_id>`, so PagerDuty folds repeats into one incident and the
resolve closes the incident the trigger opened. PagerDuty answers 202 on success; 429 and 5xx are
retried and any other refusal (400 for a bad routing key or body) is permanent.

A channel's test-send must not page anyone or leave an incident behind: it sends a `trigger`
with severity `info` (whatever the channel is set to) and `dedup_key = spanlight-test-<channel_id>`,
then a `resolve` with the same key. A failed resolve is logged and does not fail the delivery, so
a retry cannot trigger a second time.

An opened insight sends `trigger` with `dedup_key = spanlight-insight-<fingerprint>`, severity
`critical` (whatever the channel is set to: only critical insights notify) and the insight's title
as the summary. Insights never send `resolve`: an insight resolves after 24 quiet hours, which is
not a recovery signal, so the incident is closed by a person.

An alert's `trigger` is skipped when its event has already resolved. A trigger that was retried
after a PagerDuty outage can reach PagerDuty after the `resolve` that followed it, and would open
an incident nothing closes.
"""

import json
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.channels.http import SharedClient, post_checked
from app.alerts.channels.target import BAD_PAYLOAD, resolve_target, summary_event
from app.alerts.payload import AlertPayload, ChannelTestPayload, InsightPayload, parse_payload
from app.alerts.subject import alert_subject, subject_safe
from app.db.models import AlertChannelKind, AlertEvent
from app.db.rls import bind_project
from app.notifications.registry import DeliveryError, PermanentDeliveryError

if TYPE_CHECKING:
    from app.config import Settings

NO_ROUTING_KEY = "The PagerDuty channel has no routing key"
DEFAULT_SEVERITY = "error"
TEST_SEVERITY = "info"
INSIGHT_SEVERITY = "critical"
logger = structlog.get_logger(__name__)

LINK_TEXT = "Open in Spanlight"
# PagerDuty rejects a summary longer than 1024 characters.
MAX_SUMMARY_CHARS = 1024


def build_event(
    payload: AlertPayload | InsightPayload | ChannelTestPayload,
    *,
    routing_key: str,
    channel_id: str,
    severity: str,
) -> dict[str, Any]:
    """The Events v2 request body for one alert, insight or test payload."""
    summary = alert_subject(payload)
    if isinstance(payload, AlertPayload):
        action = "resolve" if payload.event == "alert.resolved" else "trigger"
        dedup_key = f"spanlight-rule-{payload.rule.id}"
        source = payload.project.name
    elif isinstance(payload, InsightPayload):
        action = "trigger"
        dedup_key = f"spanlight-insight-{payload.insight.fingerprint}"
        source = payload.project.name
        summary = subject_safe(payload.insight.title)
    else:
        action = "trigger"
        dedup_key = f"spanlight-test-{channel_id}"
        source = payload.org.name
    summary = summary[:MAX_SUMMARY_CHARS]
    return {
        "routing_key": routing_key,
        "event_action": action,
        "dedup_key": dedup_key,
        "payload": {
            "summary": summary,
            "source": source,
            "severity": severity,
            "timestamp": payload.to_json_dict()["occurred_at"],
            "custom_details": payload.to_json_dict(),
        },
        "links": [{"href": payload.url, "text": LINK_TEXT}],
    }


class PagerDutyDeliverer:
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
        channel = await resolve_target(
            self._session_factory,
            self._settings,
            target,
            kind=AlertChannelKind.PAGERDUTY,
            label="PagerDuty",
        )
        if channel.secret is None:  # resolve_target refuses this; the check narrows the type
            raise PermanentDeliveryError(NO_ROUTING_KEY)
        try:
            parsed = parse_payload(payload)
        except ValidationError:
            logger.warning("alert_payload_unparseable", alert_event=summary_event(payload))
            raise PermanentDeliveryError(BAD_PAYLOAD) from None
        is_test = isinstance(parsed, ChannelTestPayload)
        event = build_event(
            parsed,
            routing_key=channel.secret,
            channel_id=str(channel.id),
            severity=_event_severity(parsed, channel.config),
        )
        if (
            event["event_action"] == "trigger"
            and not is_test
            and await self._already_resolved(parsed)
        ):
            return
        await self._send(event)
        if is_test:
            await self._resolve_test(event)

    async def _send(self, event: dict[str, Any]) -> None:
        body = json.dumps(event, separators=(",", ":"), ensure_ascii=False).encode()
        await post_checked(self._client.get(), self._settings.pagerduty_events_url, body=body)

    async def _resolve_test(self, trigger: dict[str, Any]) -> None:
        """Close the incident a test trigger opened. A failure is logged, never raised."""
        try:
            await self._send({**trigger, "event_action": "resolve"})
        except DeliveryError as error:
            logger.warning("pagerduty_test_resolve_failed", error=str(error))

    async def _already_resolved(
        self, payload: AlertPayload | InsightPayload | ChannelTestPayload
    ) -> bool:
        """True when the event this trigger announces has resolved (or no longer exists).

        Sending the trigger would open an incident the resolve already sent cannot close.
        """
        if not isinstance(payload, AlertPayload):
            return False
        async with self._session_factory() as db:
            await bind_project(db, payload.project.id)
            row = (
                await db.execute(
                    select(AlertEvent.resolved_at).where(AlertEvent.id == payload.event_id)
                )
            ).first()
        if row is not None and row[0] is None:
            return False
        logger.info(
            "pagerduty_trigger_skipped_resolved",
            rule_id=str(payload.rule.id),
            event_id=str(payload.event_id),
            event_found=row is not None,
        )
        return True


def _severity(config: dict[str, Any]) -> str:
    return str(config.get("severity") or DEFAULT_SEVERITY)


def _event_severity(
    payload: AlertPayload | InsightPayload | ChannelTestPayload, config: dict[str, Any]
) -> str:
    """A test pages no one; an insight is critical; an alert uses the channel's severity."""
    if isinstance(payload, ChannelTestPayload):
        return TEST_SEVERITY
    if isinstance(payload, InsightPayload):
        return INSIGHT_SEVERITY
    return _severity(config)

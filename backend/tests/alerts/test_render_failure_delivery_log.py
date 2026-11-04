"""A notification that cannot be rendered or parsed fails for good, once, where people can see it.

An alert email is rendered when it is queued; one that cannot be rendered is written to the
channel's delivery log as a `failed` delivery with the reason, never sent and never retryable
(there is no message to send). Webhook and PagerDuty deliveries whose payload cannot be parsed
are permanent failures, not retries.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts import delivery_fanout
from app.alerts.channels import pagerduty, webhook
from app.alerts.channels.resolve import ResolvedChannel
from app.alerts.delivery_fanout import EMAIL_RENDER_FAILED, enqueue_for_channel
from app.budgets.types import BudgetScope
from app.config import Settings
from app.db.models import AlertChannel, AlertChannelKind
from app.notifications.lease import claim
from app.notifications.registry import PermanentDeliveryError
from tests.alerts.test_alert_render import budget_payload
from tests.gateway_fixtures import GatewayWorkspace

FAR_FUTURE = datetime(2100, 1, 1, tzinfo=UTC)
UNPARSEABLE = {"event": "alert.fired", "summary": {"event": "alert.fired"}}


async def test_an_email_that_cannot_be_rendered_is_a_failed_delivery_in_the_log(
    gateway_workspace: GatewayWorkspace,
    gateway_settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = gateway_workspace
    async with session_factory() as db:
        await db.execute(
            text("UPDATE users SET email_verified_at = now() WHERE email = 'owner@example.com'")
        )
        await db.commit()
    created = await workspace.owner.post(
        f"/api/v1/orgs/{workspace.org_id}/alert-channels",
        json={"kind": "email", "name": "Finance", "config": {"to": ["owner@example.com"]}},
    )
    assert created.status_code == 201, created.text
    channel_id = uuid.UUID(created.json()["id"])

    def broken(payload: Any, *, channel_name: str) -> Any:
        raise KeyError("scope")

    monkeypatch.setattr(delivery_fanout, "render_alert_email", broken)
    async with session_factory() as db:
        channel = await db.scalar(select(AlertChannel).where(AlertChannel.id == channel_id))
        assert channel is not None
        queued = await enqueue_for_channel(
            db, channel, budget_payload(BudgetScope.PROJECT), settings=gateway_settings
        )
        await db.commit()
        assert queued == 1
        assert await claim(db, FAR_FUTURE) is None  # nothing for the worker to send

    url = f"/api/v1/orgs/{workspace.org_id}/alert-channels/{channel_id}/deliveries"
    listed = await workspace.owner.get(url)
    assert listed.status_code == 200, listed.text
    [delivery] = listed.json()["items"]
    assert (
        delivery["kind"],
        delivery["status"],
        delivery["attempts"],
        delivery["last_error"],
        delivery["retryable"],
        delivery["summary"]["event"],
    ) == ("email", "failed", 1, EMAIL_RENDER_FAILED, False, "budget.exceeded")

    # The row did fail, so the refusal must not say it did not.
    retried = await workspace.owner.post(f"{url}/{delivery['id']}/retry")
    assert retried.status_code == 409, retried.text
    assert retried.json()["code"] == "NOT_RETRYABLE"
    assert retried.json()["detail"] == "This delivery could not be rendered and cannot be retried."


def _channel(kind: AlertChannelKind) -> ResolvedChannel:
    return ResolvedChannel(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        kind=kind,
        name="ops",
        config={"url": "https://hooks.example.com/alert"},
        secret="secret",
    )


async def test_a_webhook_payload_that_cannot_be_parsed_is_a_permanent_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resolved(*args: Any, **kwargs: Any) -> ResolvedChannel:
        return _channel(AlertChannelKind.WEBHOOK)

    monkeypatch.setattr(webhook, "resolve_target", resolved)
    deliverer = webhook.WebhookDeliverer(None, None, None)  # type: ignore[arg-type]

    with pytest.raises(PermanentDeliveryError):
        await deliverer.deliver({"channel_id": str(uuid.uuid4()), "delivery_id": "d"}, UNPARSEABLE)


async def test_a_pagerduty_payload_that_cannot_be_parsed_is_a_permanent_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def resolved(*args: Any, **kwargs: Any) -> ResolvedChannel:
        return _channel(AlertChannelKind.PAGERDUTY)

    monkeypatch.setattr(pagerduty, "resolve_target", resolved)
    deliverer = pagerduty.PagerDutyDeliverer(None, None, None)  # type: ignore[arg-type]

    with pytest.raises(PermanentDeliveryError):
        await deliverer.deliver({"channel_id": str(uuid.uuid4()), "delivery_id": "d"}, UNPARSEABLE)

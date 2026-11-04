"""Rendering an alert for a channel, and what happens when rendering fails. Pure.

Every budget scope renders in the email, worded like the budgets page names it. A payload that
cannot be rendered is a permanent failure: retrying renders the same payload the same way, so it
is attempted once, written as a failed delivery, logged and counted as `failed`, and never raises
into the evaluation pass.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from structlog.testing import capture_logs

from app.alerts import delivery_fanout
from app.alerts.channels import slack
from app.alerts.channels.email_render import render_alert_email
from app.alerts.channels.resolve import ResolvedChannel
from app.alerts.delivery_fanout import EMAIL_RENDER_FAILED, outbox_rows_for
from app.alerts.payload import AlertPayload
from app.budgets.types import BudgetScope
from app.core.observability import NOTIFICATIONS_DELIVERED
from app.db.models import AlertChannelKind
from app.notifications.registry import PermanentDeliveryError
from tests.gateway_support import metric_delta

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
SCOPE_IDS = {
    BudgetScope.PROJECT: None,
    BudgetScope.GATEWAY_KEY: str(uuid.uuid4()),
    BudgetScope.USER: "user-42",
    BudgetScope.MODEL: "gpt-4.1-mini",
    BudgetScope.METADATA: "customer_id=<acme>",
    BudgetScope.TRACE_NAME: "answer",
}
SCOPE_WORDS = {
    BudgetScope.PROJECT: "daily budget.",
    BudgetScope.GATEWAY_KEY: "daily budget for one gateway key.",
    BudgetScope.USER: "daily budget for user user-42.",
    BudgetScope.MODEL: "daily budget for model gpt-4.1-mini.",
    BudgetScope.METADATA: "daily budget for metadata customer_id=<acme>.",
    BudgetScope.TRACE_NAME: "daily budget for trace name answer.",
}


def budget_payload(scope: BudgetScope) -> AlertPayload:
    return AlertPayload.model_validate(
        {
            "event": "budget.exceeded",
            "event_id": uuid.uuid4(),
            "occurred_at": NOW,
            "org": {"id": uuid.uuid4(), "name": "Acme"},
            "project": {"id": uuid.uuid4(), "name": "Support bot"},
            "rule": {
                "id": uuid.uuid4(),
                "name": "Daily cap",
                "kind": "budget",
                "metric": "spend",
                "comparator": "gte",
                "threshold": "1",
                "window_minutes": None,
                "filters": {},
            },
            "value": "1.5",
            "threshold": "1",
            "started_at": NOW,
            "resolved_at": None,
            "budget": {
                "id": uuid.uuid4(),
                "name": "Daily cap",
                "scope": scope.value,
                "scope_id": SCOPE_IDS[scope],
                "period": "daily",
                "amount_usd": "1",
                "spent_usd": "1.5",
                "action": "notify",
                "resets_at": NOW,
            },
            "url": "https://app.example.com/budgets",
        }
    )


@pytest.mark.parametrize("scope", list(BudgetScope))
def test_every_budget_scope_renders_in_the_email(scope: BudgetScope) -> None:
    message = render_alert_email(budget_payload(scope), channel_name="Finance")

    assert f"Support bot spent $1.50 of its $1.00 {SCOPE_WORDS[scope]}" in message.text
    assert message.html is not None
    assert "<acme>" not in message.html  # the HTML part escapes the scope value
    if scope is BudgetScope.METADATA:
        assert "customer_id=&lt;acme&gt;" in message.html


def email_channel(*recipients: str) -> ResolvedChannel:
    return ResolvedChannel(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        kind=AlertChannelKind.EMAIL,
        name="Finance",
        config={"to": list(recipients)},
        secret=None,
    )


def test_an_email_that_cannot_be_rendered_fails_once_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One already-failed row per recipient, with no message: nothing to send or retry."""
    calls: list[Any] = []

    def broken(payload: Any, *, channel_name: str) -> Any:
        calls.append(payload)
        raise KeyError("scope")

    monkeypatch.setattr(delivery_fanout, "render_alert_email", broken)
    failed = metric_delta(NOTIFICATIONS_DELIVERED, kind="email", outcome="failed")
    channel = email_channel("a@example.com", "b@example.com")

    with capture_logs() as logs:
        rows = outbox_rows_for(channel, budget_payload(BudgetScope.PROJECT), allowed_emails=None)

    assert [(row.target["to"], row.failed, set(row.payload)) for row in rows] == [
        ("a@example.com", EMAIL_RENDER_FAILED, {"summary"}),
        ("b@example.com", EMAIL_RENDER_FAILED, {"summary"}),
    ]
    assert len(calls) == 1
    assert failed.value == 2
    [log] = [entry for entry in logs if entry["event"] == "alert_render_failed"]
    assert (log["log_level"], log["channel_id"], log["error_type"]) == (
        "error",
        str(channel.id),
        "KeyError",
    )


async def test_a_slack_message_that_cannot_be_rendered_is_a_permanent_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(payload: Any) -> Any:
        raise KeyError("scope")

    async def url(target: dict[str, Any]) -> str:
        return "https://hooks.slack.example/services/x"

    monkeypatch.setattr(slack, "format_slack", broken)
    deliverer = slack.SlackDeliverer(None, None, None)  # type: ignore[arg-type]
    monkeypatch.setattr(deliverer, "_url", url)
    payload = budget_payload(BudgetScope.PROJECT).to_json_dict()

    with pytest.raises(PermanentDeliveryError, match="could not be rendered"):
        await deliverer.deliver({"channel_id": str(uuid.uuid4())}, payload)

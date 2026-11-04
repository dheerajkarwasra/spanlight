"""The alert and insight emails, built from an `AlertPayload` or an `InsightPayload`. Pure.

Built with the shared email composer, which escapes every value in the HTML part. The message is
returned with `to=""`; the fan-out fills in each recipient.
"""

from datetime import timedelta

from app.alerts.formatting import (
    COMPARATOR_WORDS,
    METRIC_LABELS,
    filter_pairs,
    format_duration,
    format_metric_value,
    format_money,
)
from app.alerts.payload import AlertPayload, BudgetPayload, InsightPayload
from app.alerts.subject import TEST_SUBJECT, alert_subject, insight_subject
from app.email.message import EmailMessage
from app.email.templates import _compose

_PERIOD_WORDS = {"daily": "daily", "monthly": "monthly"}


def render_test_email(channel_name: str, org_name: str) -> EmailMessage:
    """The test-send email."""
    return _compose(
        subject=TEST_SUBJECT,
        paragraphs=[
            f"This is a test of the {channel_name} channel in {org_name}. "
            "If you can read this, email alerts work."
        ],
    )


def render_alert_email(payload: AlertPayload, *, channel_name: str) -> EmailMessage:
    """The email for a fired, resolved or budget event."""
    footer = (
        f"You get this email because you are a recipient of the {channel_name} channel "
        f"in {payload.org.name}."
    )
    if payload.event == "budget.exceeded" and payload.budget is not None:
        body = _budget_paragraphs(payload, payload.budget)
    elif payload.event == "alert.resolved":
        body = _resolved_paragraphs(payload)
    else:
        body = _fired_paragraphs(payload)
    return _compose(subject=alert_subject(payload), paragraphs=[*body, footer])


def render_insight_email(payload: InsightPayload, *, channel_name: str) -> EmailMessage:
    """The email for an insight the Doctor opened: summary, fix, how to verify, link."""
    insight = payload.insight
    return _compose(
        subject=insight_subject(payload),
        paragraphs=[
            insight.summary,
            f"Suggested fix: {insight.suggested_fix}",
            f"How to verify: {insight.verification}",
            ("Open the insight:", payload.url),
            f"You get this email because you are a recipient of the {channel_name} channel "
            f"in {payload.org.name}.",
        ],
    )


def _filters_text(payload: AlertPayload) -> str:
    pairs = filter_pairs(payload.rule.filters)
    return f" ({', '.join(pairs)})" if pairs else ""


def _fired_paragraphs(payload: AlertPayload) -> list[str | tuple[str, str]]:
    rule = payload.rule
    label = METRIC_LABELS[rule.metric]
    value = format_metric_value(rule.metric, payload.value)
    threshold = format_metric_value(rule.metric, payload.threshold)
    words = COMPARATOR_WORDS[rule.comparator]
    window = (
        f" over the last {format_duration(timedelta(minutes=rule.window_minutes))}"
        if rule.window_minutes is not None
        else ""
    )
    return [
        f"{label} is {value} ({words} {threshold}){window} in "
        f"{payload.project.name}{_filters_text(payload)}.",
        ("Open the alert:", payload.url),
    ]


def _resolved_paragraphs(payload: AlertPayload) -> list[str | tuple[str, str]]:
    label = METRIC_LABELS[payload.rule.metric]
    value = format_metric_value(payload.rule.metric, payload.value)
    lasted = ""
    if payload.resolved_at is not None:
        lasted = f" It was firing for {format_duration(payload.resolved_at - payload.started_at)}."
    return [
        f"{label} is back to {value} in {payload.project.name}.{lasted}",
        ("Open the alert:", payload.url),
    ]


def _budget_paragraphs(payload: AlertPayload, budget: BudgetPayload) -> list[str | tuple[str, str]]:
    # Worded as the budgets page names each scope; `tests/alerts/test_alert_render.py` renders
    # every `BudgetScope`, so a new scope cannot ship without its words here.
    scope = {
        "project": "",
        "gateway_key": " for one gateway key",
        "user": f" for user {budget.scope_id}",
        "model": f" for model {budget.scope_id}",
        "metadata": f" for metadata {budget.scope_id}",
        "trace_name": f" for trace name {budget.scope_id}",
    }[budget.scope]
    spent = (
        f"{payload.project.name} spent {format_money(budget.spent_usd)} of its "
        f"{format_money(budget.amount_usd)} {_PERIOD_WORDS[budget.period]} budget{scope}."
    )
    if budget.action == "block":
        resets = budget.resets_at.strftime("%Y-%m-%d %H:%M UTC")
        effect = (
            f"Gateway calls in this scope are blocked until {resets} or until the budget is raised."
        )
    else:
        effect = "Nothing is blocked; this budget only notifies."
    return [spent, effect, ("Open budgets:", payload.url)]

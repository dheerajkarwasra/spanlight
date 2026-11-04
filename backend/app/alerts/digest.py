"""The weekly digest email, built from `DigestData`. Pure.

Built with the shared email composer, which escapes every value in the HTML part. The message is
returned with `to=""`; the job fills in each recipient. A value that is unknown is written "—"
and shows no change; it is never written as 0. A change against an unknown or zero value reads
"new". Money, rates and counts come from `alerts/formatting.py`.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from app.alerts.formatting import format_count, format_money, format_ms, format_rate
from app.alerts.subject import subject_safe
from app.email.message import EmailMessage
from app.email.templates import _compose

UNKNOWN: Final = "—"
NEW: Final = "new"
MAX_MODELS: Final = 5

_Paragraph = str | tuple[str, str]


@dataclass(frozen=True, slots=True)
class DigestProject:
    id: str
    name: str
    org_id: str
    org_name: str


@dataclass(frozen=True, slots=True)
class DigestSection:
    """An extra block of the digest (Phase 4 appends its insights this way): a title and one
    paragraph per entry."""

    title: str
    paragraphs: list[str]


@dataclass(frozen=True, slots=True)
class DigestData:
    """One project's week. `week_start` (inclusive) and `week_end` (exclusive) are UTC midnights.

    A `None` figure is unknown. `top_models` are priced models, most expensive first.
    """

    project: DigestProject
    url: str
    week_start: datetime
    week_end: datetime
    spend: Decimal | None
    previous_spend: Decimal | None
    llm_calls: Decimal | None
    previous_llm_calls: Decimal | None
    error_rate: Decimal | None
    previous_error_rate: Decimal | None
    p95_ms: Decimal | None
    top_models: list[tuple[str, Decimal]]
    alerts_fired: int
    sections: list[DigestSection] = field(default_factory=list)


def digest_subject(data: DigestData) -> str:
    spend = UNKNOWN if data.spend is None else format_money(data.spend)
    calls = UNKNOWN if data.llm_calls is None else format_count(data.llm_calls)
    return subject_safe(f"Your week in {data.project.name}: {spend} spent, {calls} LLM calls")


def render_digest(data: DigestData, unsubscribe_url: str) -> EmailMessage:
    """The weekly summary email for one project, to the recipient `unsubscribe_url` was made for."""
    body: list[_Paragraph] = [
        f"Here is how {data.project.name} did from {_day(data.week_start)} to "
        f"{_day(data.week_end - timedelta(days=1))} (UTC).",
        *_figures(data),
        *_top_models(data.top_models),
        _alerts_line(data.alerts_fired),
    ]
    for section in data.sections:
        body.extend([section.title, *section.paragraphs])
    body.append((f"Open {data.project.name}:", data.url))
    body.append(
        f"You get this weekly summary as a member of {data.project.org_name}. "
        "Project admins can turn it off in project settings."
    )
    body.append(("To stop getting it for every project, unsubscribe here:", unsubscribe_url))
    return _compose(subject=digest_subject(data), paragraphs=body)


def _day(moment: datetime | date) -> str:
    return f"{moment:%b} {moment.day}"


def _figures(data: DigestData) -> list[str]:
    spend = _figure(
        "Spend", data.spend, format_money, _percent_change(data.spend, data.previous_spend)
    )
    calls = _figure(
        "LLM calls",
        data.llm_calls,
        format_count,
        _percent_change(data.llm_calls, data.previous_llm_calls),
    )
    errors = _figure(
        "Error rate",
        data.error_rate,
        format_rate,
        _points_change(data.error_rate, data.previous_error_rate),
    )
    latency = _figure("p95 latency", data.p95_ms, format_ms, None)
    return [spend, calls, errors, latency]


def _figure(
    label: str, value: Decimal | None, fmt: Callable[[Decimal], str], change: str | None
) -> str:
    if value is None:
        return f"{label} {UNKNOWN}"
    text = f"{label} {fmt(value)}"
    return f"{text} ({change})" if change is not None else text


def _percent_change(current: Decimal | None, previous: Decimal | None) -> str | None:
    """`+12 %`, `-8 %` or `0 %`.

    "new" against an unknown or zero start; None (no change text) without a current value and
    when both weeks are zero.
    """
    if current is None or (previous == 0 and current == 0):
        return None
    if previous is None or previous == 0:
        return NEW
    percent = ((current - previous) / previous * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return f"{int(percent):+d} %" if percent else "0 %"


def _points_change(current: Decimal | None, previous: Decimal | None) -> str | None:
    """Percentage points between two rates: `+1.2 pts`, `-0.4 pts`, `0.0 pts`.

    Like `_percent_change`: "new" against an unknown or zero rate, None without a current value
    and when both weeks are zero.
    """
    if current is None or (previous == 0 and current == 0):
        return None
    if previous is None or previous == 0:
        return NEW
    points = ((current - previous) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{points:+.1f} pts" if points else "0.0 pts"


def _top_models(models: list[tuple[str, Decimal]]) -> list[str]:
    if not models:
        return []
    rows = [f"{model} · {format_money(cost)}" for model, cost in models[:MAX_MODELS]]
    return ["Top models by cost", *rows]


def _alerts_line(count: int) -> str:
    if count == 0:
        return "No alerts fired this week."
    return f"{count} alert fired this week." if count == 1 else f"{count} alerts fired this week."

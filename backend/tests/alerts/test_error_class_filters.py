"""Alert rules on failed calls of one error class or rate-limit kind, and the `error_calls` metric.

Failure modes pinned here: `error_calls` unknown (`None`) for a quiet window instead of 0; a class
filter that narrows the denominator of `error_rate` too, so a class rate reads as a share of the
failures instead of the calls; the rate-limit kind ignored once a class is set; class counts that
jump at the rollup/raw boundary because the two halves count different spans (fault-injected
spans are left out of the error rollup, so the raw half must leave them out as well); the filters
accepted on a metric they cannot narrow, or with a value no span can have.
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.alerts.metrics import metric_value, plan_window, rollup_split
from app.alerts.rules import metric_filters
from app.alerts.types import Metric, MetricFilters
from app.api.window import TimeWindow
from app.db.rls import bind_project, bypass_rls
from app.rollups.compute import floor_hour
from tests.conftest import BrowserFactory
from tests.helpers import create_workspace
from tests.jobs.bypass_support import SessionFactory, new_projects
from tests.rollups.error_support import compute

USER = "user-1"
FAULT = {"spanlight.fault.scenario": "rate_limited"}
RATE_LIMIT = MetricFilters(error_class="rate_limit")
SPEND_CAP = MetricFilters(error_class="rate_limit", rate_limit_kind="spend_cap")
CLASS_MESSAGE = "filters.error_class: applies to error_rate and error_calls"

_INSERT_TRACE = text(
    "INSERT INTO traces (project_id, trace_id, started_at, ended_at, environment, "
    "external_user_id) VALUES (:project_id, :trace_id, :started_at, :ended_at, 'production', "
    ":user_id)"
)
_INSERT_SPAN = text(
    """
    INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, started_at, ended_at,
                       provider, model, error_class, rate_limit_kind, attributes)
    VALUES (:project_id, :trace_id, :span_id, :kind, 'call', :status, :started_at, :ended_at,
            'openai', 'gpt-4.1', :error_class, :rate_limit_kind, CAST(:attributes AS jsonb))
    """
)


@dataclass(frozen=True, slots=True)
class Call:
    """One span in a trace of its own, all made by `USER`. The default is a successful llm call."""

    started_at: datetime
    error_class: str | None = None
    rate_limit_kind: str | None = None
    span_kind: str = "llm"
    attributes: dict[str, Any] = field(default_factory=dict)
    failed: bool = False

    @property
    def status(self) -> str:
        return "error" if self.failed or self.error_class is not None else "ok"


def failed(
    at: datetime, error_class: str | None, rate_limit_kind: str | None = None, **extra: Any
) -> Call:
    return Call(at, error_class, rate_limit_kind, failed=True, **extra)


async def seed(sessions: SessionFactory, project_id: uuid.UUID, calls: list[Call]) -> None:
    traces, spans = [], []
    for call in calls:
        trace_id = uuid.uuid4().hex
        times = {"started_at": call.started_at, "ended_at": call.started_at + timedelta(seconds=1)}
        common = {"project_id": project_id, "trace_id": trace_id, **times}
        traces.append({**common, "user_id": USER})
        spans.append(
            {
                **common,
                "span_id": trace_id[:16],
                "kind": call.span_kind,
                "status": call.status,
                "error_class": call.error_class,
                "rate_limit_kind": call.rate_limit_kind,
                "attributes": json.dumps(call.attributes),
            }
        )
    async with sessions() as db:
        await bypass_rls(db)
        await db.execute(_INSERT_TRACE, traces)
        await db.execute(_INSERT_SPAN, spans)
        await db.commit()


async def read(
    sessions: SessionFactory,
    project_id: uuid.UUID,
    metric: Metric,
    window: TimeWindow,
    filters: MetricFilters,
    now: datetime,
) -> Decimal | None:
    async with sessions() as db, db.begin():
        await bind_project(db, project_id)
        value = await metric_value(db, project_id, metric, window, filters, now=now)
    return value.value


def recent() -> tuple[TimeWindow, datetime]:
    """A 30-minute window inside the raw part of a read at the returned `now`."""
    now = floor_hour(datetime.now(UTC)) + timedelta(minutes=40)
    return TimeWindow(now - timedelta(minutes=30), now), now


async def test_error_calls_counts_failed_llm_calls_and_is_zero_when_none(
    session_factory: SessionFactory,
) -> None:
    project, quiet = await new_projects(session_factory, 2)
    window, now = recent()
    at = window.start + timedelta(minutes=1)
    await seed(
        session_factory,
        project,
        [
            failed(at, "rate_limit", "requests"),
            failed(at, "timeout"),
            failed(at, None),  # a failed call stored before error classes existed
            Call(at),
            Call(at),
            failed(at, "timeout", span_kind="tool"),  # not an llm call
            failed(window.end, "timeout"),  # after the window
        ],
    )

    assert (
        await read(session_factory, project, Metric.ERROR_CALLS, window, MetricFilters(), now) == 3
    )
    assert await read(session_factory, quiet, Metric.ERROR_CALLS, window, MetricFilters(), now) == 0
    assert await read(session_factory, quiet, Metric.ERROR_CALLS, window, RATE_LIMIT, now) == 0


async def test_error_rate_with_class_filter_divides_class_errors_by_all_calls(
    session_factory: SessionFactory,
) -> None:
    project, quiet = await new_projects(session_factory, 2)
    window, now = recent()
    at = window.start + timedelta(minutes=5)
    await seed(
        session_factory,
        project,
        [
            failed(at, "rate_limit", "requests"),
            failed(at, "rate_limit", "tokens"),
            failed(at, "rate_limit", "requests", attributes=FAULT),  # injected by the fault lab
            failed(at, "timeout"),
            *[Call(at) for _ in range(6)],
        ],
    )

    rate = await read(session_factory, project, Metric.ERROR_RATE, window, RATE_LIMIT, now)
    assert rate == Decimal(2) / Decimal(10)
    calls = await read(session_factory, project, Metric.ERROR_CALLS, window, RATE_LIMIT, now)
    assert calls == 2
    # Without calls the rate stays unknown, filter or not.
    assert await read(session_factory, quiet, Metric.ERROR_RATE, window, RATE_LIMIT, now) is None


async def test_rate_limit_kind_filter_narrows_further(session_factory: SessionFactory) -> None:
    (project,) = await new_projects(session_factory, 1)
    window, now = recent()
    at = window.start + timedelta(minutes=5)
    await seed(
        session_factory,
        project,
        [
            failed(at, "rate_limit", "requests"),
            failed(at, "rate_limit", "requests"),
            failed(at, "rate_limit", "spend_cap"),
            failed(at, "provider_5xx", "overloaded"),
            Call(at),
        ],
    )

    async def calls(filters: MetricFilters) -> Decimal | None:
        return await read(session_factory, project, Metric.ERROR_CALLS, window, filters, now)

    assert await calls(RATE_LIMIT) == 3
    assert await calls(SPEND_CAP) == 1
    assert await calls(MetricFilters(rate_limit_kind="overloaded")) == 1
    assert await calls(MetricFilters(error_class="timeout", rate_limit_kind="requests")) == 0
    rate = await read(session_factory, project, Metric.ERROR_RATE, window, SPEND_CAP, now)
    assert rate == Decimal(1) / Decimal(5)


async def test_rollup_and_raw_split_agrees_with_raw_only(session_factory: SessionFactory) -> None:
    (project,) = await new_projects(session_factory, 1)
    hour = floor_hour(datetime.now(UTC)) - timedelta(days=1)
    now = hour + timedelta(minutes=31)
    split = rollup_split(now)  # hour - 1 h
    window = TimeWindow(hour - timedelta(hours=5, minutes=-15), now)
    plan = plan_window(window, split)
    assert plan.rollup == (hour - timedelta(hours=4), split)
    # A spend-cap refusal, another rate limit, an injected fault and a success in every part of
    # the window: the partial head hour, the rolled-up hours, the raw hours from the split on.
    points = [
        window.start + timedelta(minutes=1),
        hour - timedelta(hours=4),
        hour - timedelta(hours=2, minutes=1),
        split - timedelta(seconds=1),
        split,
        now - timedelta(seconds=1),
    ]
    calls = [
        call
        for at in points
        for call in (
            failed(at, "rate_limit", "spend_cap"),
            failed(at, "rate_limit", "tokens"),
            failed(at, "rate_limit", "spend_cap", attributes=FAULT),
            Call(at),
        )
    ]
    await seed(session_factory, project, calls)
    await compute(session_factory, project, floor_hour(window.start), split)

    for metric in (Metric.ERROR_CALLS, Metric.ERROR_RATE):
        for filters in (SPEND_CAP, RATE_LIMIT):
            raw_only = MetricFilters(
                error_class=filters.error_class,
                rate_limit_kind=filters.rate_limit_kind,
                external_user_id=USER,
            )
            assert raw_only.forces_raw
            split_read = await read(session_factory, project, metric, window, filters, now)
            raw_read = await read(session_factory, project, metric, window, raw_only, now)
            assert split_read == raw_read, (metric, filters)
    spend_caps = await read(session_factory, project, Metric.ERROR_CALLS, window, SPEND_CAP, now)
    assert spend_caps == len(points)
    rate = await read(session_factory, project, Metric.ERROR_RATE, window, RATE_LIMIT, now)
    assert rate == Decimal(2 * len(points)) / Decimal(4 * len(points))


def _definition(metric: str, filters: dict[str, str]) -> dict[str, Any]:
    """A threshold rule as the preview takes it."""
    return {
        "kind": "threshold",
        "metric": metric,
        "comparator": "gt",
        "threshold": "0",
        "window_minutes": 60,
        "filters": filters,
    }


def _rule(metric: str, filters: dict[str, str]) -> dict[str, Any]:
    return {"name": "Spend cap reached", **_definition(metric, filters)}


def _messages(body: dict[str, Any]) -> list[str]:
    return [error["message"] for error in body.get("errors", [])]


async def test_filter_refused_on_p95_ms_with_the_exact_message(
    browser_factory: BrowserFactory,
) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    url = f"/api/v1/projects/{workspace.project_id}/alert-rules"

    response = await workspace.owner.post(url, json=_rule("p95_ms", {"error_class": "timeout"}))
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert any(CLASS_MESSAGE in message for message in _messages(body)), body
    # A kind alone is refused the same way, naming the key that was sent.
    kind_only = _rule("llm_calls", {"rate_limit_kind": "spend_cap"})
    response = await workspace.owner.post(url, json=kind_only)
    assert response.status_code == 422, response.text
    kind_message = CLASS_MESSAGE.replace("error_class", "rate_limit_kind")
    assert any(kind_message in message for message in _messages(response.json()))
    preview = await workspace.owner.post(
        f"{url}/preview", json=_definition("cost_usd", {"error_class": "rate_limit"})
    )
    assert preview.status_code == 422, preview.text
    assert any(CLASS_MESSAGE in message for message in _messages(preview.json()))


async def test_unknown_class_value_is_422(browser_factory: BrowserFactory) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    url = f"/api/v1/projects/{workspace.project_id}/alert-rules"

    for filters in ({"error_class": "burst"}, {"rate_limit_kind": "burst"}):
        response = await workspace.owner.post(url, json=_rule("error_calls", filters))
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "VALIDATION_ERROR"
        key = next(iter(filters))
        assert any(f"{key}: must be one of" in m for m in _messages(response.json())), filters


async def test_preview_accepts_the_filters(
    browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    project = uuid.UUID(workspace.project_id)
    url = f"/api/v1/projects/{project}/alert-rules"
    # The preview's newest 60-minute window ends at the start of the current hour.
    at = floor_hour(datetime.now(UTC)) - timedelta(minutes=30)
    await seed(
        session_factory,
        project,
        [
            failed(at, "rate_limit", "spend_cap"),
            failed(at, "rate_limit", "spend_cap"),
            failed(at, "rate_limit", "requests"),
            Call(at),
        ],
    )
    filters = {"error_class": "rate_limit", "rate_limit_kind": "spend_cap"}

    preview = await workspace.owner.post(f"{url}/preview", json=_definition("error_calls", filters))
    assert preview.status_code == 200, preview.text
    points = preview.json()["points"]
    assert points[-1]["value"] == "2"
    assert {point["value"] for point in points[:-1]} == {"0"}
    rate = await workspace.owner.post(f"{url}/preview", json=_definition("error_rate", filters))
    assert rate.status_code == 200, rate.text
    assert Decimal(rate.json()["points"][-1]["value"]) == Decimal("0.5")

    created = await workspace.owner.post(url, json=_rule("error_calls", filters))
    assert created.status_code == 201, created.text
    assert (created.json()["metric"], created.json()["filters"]) == ("error_calls", filters)
    assert metric_filters(created.json()["filters"]) == SPEND_CAP

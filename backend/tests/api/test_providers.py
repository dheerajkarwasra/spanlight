"""The provider health routes: health per provider and the error timeseries (the error samples
are in `test_provider_samples.py`).

Failure modes pinned here: an error rate of `0` for a provider with no calls, or a float instead
of a decimal string; classes in the wrong order or zero classes listed; a retry count or cost
summed over a trace whose figure is unknown (it must be `null`); incidents of the wrong provider,
outside the window or not API-affecting on a card; counts that jump at the rollup/raw split, or a
bucket that read a rollup hour without saying so; a route that does not count its use.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.api.window import TimeWindow
from app.config import Settings
from app.core.observability import FEATURE_USED
from app.db.rls import bind_project
from app.providers.retry_queries import read_retry_totals
from app.rollups.compute import HOUR, floor_hour
from tests.api.providers_support import (
    Call,
    SessionFactory,
    compute,
    failed,
    health_path,
    pinned_app,
    pinned_now,
    seed_calls,
    seed_incident,
    seed_trace,
    window,
    z,
)
from tests.conftest import BrowserFactory
from tests.gateway_support import metric_delta
from tests.helpers import Browser, create_workspace

RETRY_AFTER = {"spanlight.gateway.retry_after_s": 12}
# What the gateway stores when the provider sent no Retry-After: the key, as null.
NO_RETRY_AFTER = {"spanlight.gateway.retry_after_s": None}


@pytest.fixture
def now() -> datetime:
    return pinned_now()


@pytest.fixture
async def app(settings: Settings, now: datetime) -> AsyncIterator[Any]:
    async with pinned_app(settings, now) as application:
        yield application


@pytest.fixture
async def owner(browser_factory: BrowserFactory) -> tuple[Browser, uuid.UUID]:
    browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(browser)
    return browser, uuid.UUID(workspace.project_id)


# --- health ------------------------------------------------------------------------------------


async def test_health_reports_each_provider_seen_in_the_window(
    owner: tuple[Browser, uuid.UUID], session_factory: SessionFactory, now: datetime
) -> None:
    browser, project_id = owner
    at = now - timedelta(minutes=20)
    await seed_trace(
        session_factory,
        project_id,
        [Call(at), Call(at), failed(at, "rate_limit", "tokens", attributes=RETRY_AFTER)],
        retry_calls=2,
        retry_cost_usd=Decimal("0.01"),
    )
    await seed_trace(
        session_factory,
        project_id,
        [
            failed(at, "provider_5xx"),
            failed(at, "rate_limit", "spend_cap", attributes=NO_RETRY_AFTER),
        ],
        retry_calls=None,
        retry_cost_usd=None,
    )
    await seed_trace(
        session_factory,
        project_id,
        [Call(at, provider="anthropic"), Call(at, provider="anthropic", kind="tool")],
        retry_calls=1,
        retry_cost_usd=Decimal("0.002"),
    )
    # Outside the window: not counted.
    await seed_calls(session_factory, project_id, [failed(now - timedelta(hours=3), "timeout")])
    ongoing = await seed_incident(session_factory, "ongoing", now - timedelta(minutes=50))
    await seed_incident(
        session_factory, "over", now - timedelta(hours=5), resolved_at=now - timedelta(hours=4)
    )
    await seed_incident(session_factory, "web", now - timedelta(minutes=40), api_affected=False)
    await seed_incident(session_factory, "gemini", now - timedelta(minutes=40), provider="google")

    response = await browser.get(
        health_path(project_id), params=window(now - timedelta(hours=1), now)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["approximate"] is False
    assert body["window"] == {
        "start": (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "end": now.isoformat().replace("+00:00", "Z"),
    }
    assert isinstance(body["retry_cost_note"], str) and body["retry_cost_note"]
    providers = {p["provider"]: p for p in body["providers"]}
    assert list(providers) == ["openai", "anthropic"]  # busiest first
    openai = providers["openai"]
    assert (openai["llm_calls"], openai["errors"], openai["error_rate"]) == (5, 3, "0.6")
    assert openai["by_class"] == [
        {"error_class": "rate_limit", "count": 2},
        {"error_class": "provider_5xx", "count": 1},
    ]
    assert openai["rate_limit_kinds"] == [
        {"kind": "tokens", "count": 1, "with_retry_after": 1},
        {"kind": "spend_cap", "count": 1, "with_retry_after": 0},
    ]
    # One trace was stored before retries were tracked: neither figure is a partial sum.
    assert openai["retry_calls"] is None
    assert openai["retry_cost_usd"] is None
    assert openai["retry_cost_unknown"] is True
    assert [incident["id"] for incident in openai["incidents"]] == [ongoing]
    assert set(openai["incidents"][0]) == {
        "id",
        "title",
        "impact",
        "status",
        "started_at",
        "resolved_at",
        "url",
    }
    anthropic = providers["anthropic"]
    assert (anthropic["llm_calls"], anthropic["errors"], anthropic["error_rate"]) == (1, 0, "0")
    assert anthropic["by_class"] == [] and anthropic["rate_limit_kinds"] == []
    # The untracked trace is not attributed (its spans are never read for it), so it could be any
    # provider's: every card's retry figures are unknown, never a partial sum.
    assert anthropic["retry_calls"] is None
    assert (anthropic["retry_cost_usd"], anthropic["retry_cost_unknown"]) == (None, True)
    assert anthropic["incidents"] == []


async def test_retries_of_tracked_traces_are_attributed_to_their_provider(
    owner: tuple[Browser, uuid.UUID], session_factory: SessionFactory, now: datetime
) -> None:
    browser, project_id = owner
    at = now - timedelta(minutes=20)
    await seed_trace(
        session_factory, project_id, [Call(at)], retry_calls=2, retry_cost_usd=Decimal("0.01")
    )
    await seed_trace(
        session_factory,
        project_id,
        [Call(at, provider="anthropic")],
        retry_calls=1,
        retry_cost_usd=Decimal("0.002"),
    )

    response = await browser.get(health_path(project_id), params=window(now - HOUR, now))

    assert response.status_code == 200, response.text
    providers = {p["provider"]: p for p in response.json()["providers"]}
    assert (providers["openai"]["retry_calls"], providers["anthropic"]["retry_calls"]) == (2, 1)
    assert Decimal(providers["anthropic"]["retry_cost_usd"]) == Decimal("0.002")
    assert providers["anthropic"]["retry_cost_unknown"] is False


async def test_untracked_traces_are_unknown_without_reading_their_spans(
    owner: tuple[Browser, uuid.UUID], session_factory: SessionFactory, now: datetime
) -> None:
    _, project_id = owner
    at = now - timedelta(minutes=20)
    await seed_trace(
        session_factory,
        project_id,
        [Call(at, provider="google")],
        retry_calls=None,
        retry_cost_usd=None,
    )
    await seed_trace(
        session_factory, project_id, [Call(at)], retry_calls=1, retry_cost_usd=Decimal("0.5")
    )

    async with session_factory() as db, db.begin():
        await bind_project(db, project_id)
        report = await read_retry_totals(db, project_id, TimeWindow(now - HOUR, now), None)

    assert report.untracked is True
    # Only the trace with retries was attributed; the untracked one was not joined to its spans.
    assert set(report.by_provider) == {"openai"}
    assert report.by_provider["openai"].retry_calls == 1
    # Every provider's figures are unknown while an untracked trace is in the window.
    assert report.for_provider("google").retry_calls is None
    assert report.for_provider("openai").retry_calls is None
    assert report.for_provider("openai").cost_unknown is True


async def test_a_capitalised_provider_lists_its_incidents(
    owner: tuple[Browser, uuid.UUID], session_factory: SessionFactory, now: datetime
) -> None:
    browser, project_id = owner
    await seed_trace(session_factory, project_id, [Call(now - timedelta(minutes=10), "OpenAI")])
    incident = await seed_incident(session_factory, "caps", now - timedelta(minutes=30))

    response = await browser.get(health_path(project_id), params=window(now - HOUR, now))

    assert response.status_code == 200, response.text
    [card] = response.json()["providers"]
    assert card["provider"] == "OpenAI"
    assert [found["id"] for found in card["incidents"]] == [incident]


async def test_an_unpriced_retry_keeps_the_count_and_hides_the_cost(
    owner: tuple[Browser, uuid.UUID], session_factory: SessionFactory, now: datetime
) -> None:
    browser, project_id = owner
    at = now - timedelta(minutes=10)
    await seed_trace(session_factory, project_id, [Call(at)], retry_calls=2, retry_cost_usd=None)
    await seed_trace(
        session_factory, project_id, [Call(at)], retry_calls=1, retry_cost_usd=Decimal("0.5")
    )
    await seed_trace(session_factory, project_id, [Call(at)])  # a known zero

    response = await browser.get(health_path(project_id), params=window(now - HOUR, now))

    assert response.status_code == 200, response.text
    [openai] = response.json()["providers"]
    assert openai["retry_calls"] == 3
    assert (openai["retry_cost_usd"], openai["retry_cost_unknown"]) == (None, True)


async def test_health_of_a_quiet_window_lists_no_provider(
    owner: tuple[Browser, uuid.UUID], now: datetime
) -> None:
    browser, project_id = owner
    response = await browser.get(health_path(project_id), params=window(now - HOUR, now))
    assert response.status_code == 200, response.text
    assert response.json()["providers"] == []


async def test_health_filters_by_the_trace_environment(
    owner: tuple[Browser, uuid.UUID], session_factory: SessionFactory, now: datetime
) -> None:
    browser, project_id = owner
    at = now - timedelta(minutes=10)
    await seed_calls(session_factory, project_id, [failed(at, "timeout")], environment="staging")
    await seed_calls(session_factory, project_id, [Call(at)], environment="production")

    params = {**window(now - HOUR, now), "environment": "staging"}
    response = await browser.get(health_path(project_id), params=params)

    assert response.status_code == 200, response.text
    [openai] = response.json()["providers"]
    assert (openai["llm_calls"], openai["errors"], openai["error_rate"]) == (1, 1, "1")


async def test_health_adds_rollup_hours_and_raw_spans_exactly(
    owner: tuple[Browser, uuid.UUID], session_factory: SessionFactory, now: datetime
) -> None:
    browser, project_id = owner
    hour = floor_hour(now)
    old = [Call(hour - 3 * HOUR + timedelta(minutes=5)), failed(hour - 3 * HOUR, "timeout")]
    await seed_calls(session_factory, project_id, old)
    await compute(session_factory, project_id, hour - 4 * HOUR, hour)
    await seed_calls(session_factory, project_id, [failed(now - timedelta(minutes=5), "auth")])

    params = window(hour - 3 * HOUR, now)
    response = await browser.get(health_path(project_id), params=params)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["approximate"] is True
    [openai] = body["providers"]
    assert (openai["llm_calls"], openai["errors"], openai["error_rate"]) == (3, 2, "0.666667")
    assert openai["by_class"] == [
        {"error_class": "timeout", "count": 1},
        {"error_class": "auth", "count": 1},
    ]


# --- timeseries --------------------------------------------------------------------------------


async def test_timeseries_straddles_the_rollup_split(
    owner: tuple[Browser, uuid.UUID], session_factory: SessionFactory, now: datetime
) -> None:
    browser, project_id = owner
    hour = floor_hour(now)  # split = hour - 1 h: earlier whole hours are read from the rollup
    calls = [
        failed(hour - 4 * HOUR + timedelta(minutes=40), "rate_limit", "requests"),
        failed(hour - 2 * HOUR + timedelta(minutes=10), "provider_5xx"),
        failed(hour - 2 * HOUR + timedelta(minutes=20), "provider_5xx"),
        failed(hour - 2 * HOUR + timedelta(minutes=30), "timeout"),
        Call(hour - 2 * HOUR + timedelta(minutes=40)),
        failed(hour + timedelta(minutes=10), "rate_limit", "tokens", provider="anthropic"),
    ]
    await seed_calls(session_factory, project_id, calls)
    await compute(session_factory, project_id, hour - 5 * HOUR, hour)
    # Written after the rollup ran: its hour is read from the rollup, which has not seen it.
    await seed_calls(session_factory, project_id, [failed(hour - 2 * HOUR, "auth")])

    params = window(hour - 3 * HOUR - timedelta(minutes=30), now)
    response = await browser.get(health_path(project_id, "errors/timeseries"), params=params)

    assert response.status_code == 200, response.text
    assert response.json() == [
        {"bucket_start": z(hour - 4 * HOUR), "by_class": {"rate_limit": 1}, "approximate": False},
        {"bucket_start": z(hour - 3 * HOUR), "by_class": {}, "approximate": True},
        {
            "bucket_start": z(hour - 2 * HOUR),
            "by_class": {"provider_5xx": 2, "timeout": 1},
            "approximate": True,
        },
        {"bucket_start": z(hour - HOUR), "by_class": {}, "approximate": False},
        {"bucket_start": z(hour), "by_class": {"rate_limit": 1}, "approximate": False},
    ]

    by_provider = {**params, "provider": "anthropic"}
    response = await browser.get(health_path(project_id, "errors/timeseries"), params=by_provider)
    assert response.status_code == 200, response.text
    assert [bucket["by_class"] for bucket in response.json()] == [{}, {}, {}, {}, {"rate_limit": 1}]


# --- usage counter -----------------------------------------------------------------------------


async def test_each_route_counts_one_use_of_its_feature(owner: tuple[Browser, uuid.UUID]) -> None:
    browser, project_id = owner
    health = metric_delta(FEATURE_USED, feature="provider_health")
    samples = metric_delta(FEATURE_USED, feature="provider_errors_samples")

    assert (await browser.get(health_path(project_id))).status_code == 200
    assert (health.value, samples.value) == (1, 0)
    assert (await browser.get(health_path(project_id, "errors/samples"))).status_code == 200
    assert (health.value, samples.value) == (1, 1)
    # The chart is part of the health page: it does not count a second use of it.
    assert (await browser.get(health_path(project_id, "errors/timeseries"))).status_code == 200
    assert (health.value, samples.value) == (1, 1)

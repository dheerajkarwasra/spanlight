"""`GET /projects/{project_id}/providers/errors/samples`: the newest failed llm calls.

Failure modes pinned here: samples out of order; a cursor that repeats or skips a span; a window
over 24 h that is not clamped (or a short one reported as clamped); a lab fault or a successful
call sampled as a failure; a filter ignored; an unknown enum value, limit or cursor accepted
instead of refused.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

import pytest

from app.config import Settings
from tests.api.providers_support import (
    Call,
    SessionFactory,
    failed,
    health_path,
    pinned_app,
    pinned_now,
    seed_calls,
    window,
    z,
)
from tests.conftest import BrowserFactory
from tests.helpers import Browser, create_workspace


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


async def test_samples_are_newest_first_with_a_working_cursor(
    owner: tuple[Browser, uuid.UUID], session_factory: SessionFactory, now: datetime
) -> None:
    browser, project_id = owner
    attributes = {
        "spanlight.gateway.retry_after_s": 7.5,
        "spanlight.gateway.attempts": 3,
        "provider.request_id": "req_123",
    }
    newest = failed(
        now - timedelta(minutes=1),
        "rate_limit",
        "tokens",
        provider_error_type="rate_limit_error",
        provider_error_code="rate_limit_exceeded",
        status_message="429 Too Many Requests",
        attributes=attributes,
    )
    middle = failed(now - timedelta(hours=2), "timeout")
    oldest = failed(now - timedelta(hours=20), "provider_5xx")
    too_old = failed(now - timedelta(hours=30), "provider_5xx")
    ok = Call(now - timedelta(minutes=2))
    lab = {"spanlight.fault.scenario": "rate_limited"}
    fault = failed(now - timedelta(minutes=3), "rate_limit", "unknown", attributes=lab)
    await seed_calls(session_factory, project_id, [newest, middle, oldest, too_old, ok, fault])
    path = health_path(project_id, "errors/samples")

    first = await browser.get(path, params={**window(now - timedelta(hours=48), now), "limit": 2})

    assert first.status_code == 200, first.text
    page = first.json()
    assert page["clamped"] is True
    assert [item["span_id"] for item in page["items"]] == [newest.span_id, middle.span_id]
    assert page["items"][0] == {
        "trace_id": page["items"][0]["trace_id"],
        "span_id": newest.span_id,
        "started_at": z(newest.started_at),
        "model": "gpt-4.1",
        "status_message": "429 Too Many Requests",
        "error_class": "rate_limit",
        "rate_limit_kind": "tokens",
        "provider_error_type": "rate_limit_error",
        "provider_error_code": "rate_limit_exceeded",
        "retry_after_s": 7.5,
        "attempts": 3,
        "request_id": "req_123",
    }
    assert page["next_cursor"]

    params = {**window(now - timedelta(hours=48), now), "limit": 2, "cursor": page["next_cursor"]}
    second = await browser.get(path, params=params)
    assert second.status_code == 200, second.text
    assert [item["span_id"] for item in second.json()["items"]] == [oldest.span_id]
    assert second.json()["next_cursor"] is None

    short = await browser.get(path, params=window(now - timedelta(hours=3), now))
    assert short.json()["clamped"] is False
    assert [item["span_id"] for item in short.json()["items"]] == [newest.span_id, middle.span_id]


async def test_samples_filter_by_class_and_kind(
    owner: tuple[Browser, uuid.UUID], session_factory: SessionFactory, now: datetime
) -> None:
    browser, project_id = owner
    tokens = failed(now - timedelta(minutes=1), "rate_limit", "tokens")
    spend = failed(now - timedelta(minutes=2), "rate_limit", "spend_cap")
    timeout = failed(now - timedelta(minutes=3), "timeout", provider="anthropic")
    await seed_calls(session_factory, project_id, [tokens, spend, timeout])
    path = health_path(project_id, "errors/samples")

    async def span_ids(**params: str) -> list[str]:
        response = await browser.get(
            path, params={**window(now - timedelta(hours=1), now), **params}
        )
        assert response.status_code == 200, response.text
        return [item["span_id"] for item in response.json()["items"]]

    assert await span_ids(error_class="rate_limit") == [tokens.span_id, spend.span_id]
    assert await span_ids(rate_limit_kind="spend_cap") == [spend.span_id]
    assert await span_ids(provider="anthropic") == [timeout.span_id]
    assert await span_ids(environment="staging") == []


@pytest.mark.parametrize(
    "params",
    [
        {"rate_limit_kind": "per_minute"},
        {"error_class": "slow"},
        {"limit": "51"},
        {"limit": "0"},
        {"cursor": "not-a-cursor"},
    ],
)
async def test_samples_refuse_unknown_values(
    owner: tuple[Browser, uuid.UUID], params: dict[str, str]
) -> None:
    browser, project_id = owner
    response = await browser.get(health_path(project_id, "errors/samples"), params=params)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"

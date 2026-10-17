"""`GET /projects/{project_id}/provider-incidents`: the incident list and the feeds' poll state.

Failure modes pinned here: the detector's 15-minute grace leaking into the list (an incident
resolved just before the window would show); a window that misses an incident still open, or
one that started before it; incidents that do not touch the API shown by default; a cursor that
repeats or skips an incident; `polling` that does not follow the server setting; a source block
missing the fields the stale badge is computed from.
"""

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from app.config import Settings
from app.core.observability import FEATURE_USED
from tests.api.providers_support import SessionFactory, pinned_app, pinned_now, seed_incident
from tests.conftest import BrowserFactory
from tests.gateway_support import metric_delta
from tests.helpers import Browser, create_workspace

SOURCE_FIELDS = {
    "provider",
    "last_success_at",
    "last_polled_at",
    "last_outcome",
    "consecutive_failures",
}


@pytest.fixture
def now() -> datetime:
    return pinned_now()


@pytest.fixture
async def app(
    settings: Settings, now: datetime, request: pytest.FixtureRequest
) -> AsyncIterator[Any]:
    """The app; `@pytest.mark.parametrize("app", [False], indirect=True)` turns polling off."""
    polling = getattr(request, "param", True)
    configured = settings.model_copy(update={"provider_status_polling_enabled": polling})
    async with pinned_app(configured, now) as application:
        yield application


@pytest.fixture
async def incidents_path(browser_factory: BrowserFactory) -> tuple[Browser, str]:
    browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(browser)
    return browser, f"/api/v1/projects/{workspace.project_id}/provider-incidents"


def window(start: datetime, end: datetime, **extra: Any) -> dict[str, Any]:
    return {"from": start.isoformat(), "to": end.isoformat(), **extra}


async def test_the_list_holds_the_incidents_that_overlap_the_window(
    incidents_path: tuple[Browser, str], session_factory: SessionFactory, now: datetime
) -> None:
    browser, path = incidents_path
    start = now - timedelta(hours=6)
    end = start + timedelta(hours=2)
    before = await seed_incident(
        session_factory,
        "before",
        start - timedelta(hours=1),
        resolved_at=start + timedelta(minutes=5),
    )
    inside = await seed_incident(session_factory, "inside", start + timedelta(minutes=30))
    # Resolved ten minutes before the window: the detector's grace would include it, the list not.
    await seed_incident(
        session_factory,
        "drained",
        start - timedelta(hours=1),
        resolved_at=start - timedelta(minutes=10),
    )
    await seed_incident(session_factory, "after", end + timedelta(minutes=1))
    other = await seed_incident(
        session_factory, "other", start + timedelta(minutes=10), provider="anthropic"
    )

    response = await browser.get(path, params=window(start, end))

    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["id"] for item in body["items"]] == [inside, other, before]
    assert body["next_cursor"] is None
    assert set(body["items"][0]) == {
        "id",
        "provider",
        "title",
        "impact",
        "status",
        "api_affected",
        "components",
        "started_at",
        "resolved_at",
        "url",
        "latest_update",
    }

    only_openai = await browser.get(path, params=window(start, end, provider="openai"))
    assert [item["id"] for item in only_openai.json()["items"]] == [inside, before]


async def test_api_only_is_the_default_and_false_includes_the_rest(
    incidents_path: tuple[Browser, str], session_factory: SessionFactory, now: datetime
) -> None:
    browser, path = incidents_path
    api = await seed_incident(session_factory, "api", now - timedelta(hours=2))
    web = await seed_incident(session_factory, "web", now - timedelta(hours=1), api_affected=False)
    params = window(now - timedelta(hours=3), now)

    default = await browser.get(path, params=params)
    everything = await browser.get(path, params={**params, "api_only": "false"})

    assert [item["id"] for item in default.json()["items"]] == [api]
    assert [item["id"] for item in everything.json()["items"]] == [web, api]
    assert everything.json()["items"][0]["api_affected"] is False


async def test_the_cursor_pages_through_without_repeats(
    incidents_path: tuple[Browser, str], session_factory: SessionFactory, now: datetime
) -> None:
    browser, path = incidents_path
    ids = [
        await seed_incident(session_factory, f"i{n}", now - timedelta(hours=n)) for n in (1, 2, 3)
    ]
    params = window(now - timedelta(hours=4), now, limit=2)

    first = (await browser.get(path, params=params)).json()
    second = (await browser.get(path, params={**params, "cursor": first["next_cursor"]})).json()

    assert [item["id"] for item in first["items"]] == ids[:2]
    assert [item["id"] for item in second["items"]] == ids[2:]
    assert second["next_cursor"] is None


@pytest.mark.parametrize(
    "params", [{"limit": "101"}, {"provider": "aws"}, {"cursor": "x"}, {"api_only": "maybe"}]
)
async def test_unknown_values_are_refused(
    incidents_path: tuple[Browser, str], params: dict[str, str]
) -> None:
    browser, path = incidents_path
    response = await browser.get(path, params=params)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_the_sources_block_carries_each_feeds_poll_state(
    incidents_path: tuple[Browser, str], session_factory: SessionFactory, now: datetime
) -> None:
    browser, path = incidents_path
    async with session_factory() as db:
        await db.execute(
            text(
                "UPDATE provider_status_sources SET last_polled_at = :now, "
                "last_success_at = :success, last_outcome = 'http_error', "
                "consecutive_failures = 36, last_error_type = 'ConnectError' "
                "WHERE provider = 'anthropic'"
            ),
            {"now": now, "success": now - timedelta(hours=3)},
        )
        await db.commit()

    body = (await browser.get(path)).json()

    assert body["polling"] == "enabled"
    sources = {source["provider"]: source for source in body["sources"]}
    assert set(sources) == {"anthropic", "google", "openai"}
    assert all(set(source) == SOURCE_FIELDS for source in body["sources"])
    assert sources["anthropic"] == {
        "provider": "anthropic",
        "last_success_at": (now - timedelta(hours=3)).isoformat().replace("+00:00", "Z"),
        "last_polled_at": now.isoformat().replace("+00:00", "Z"),
        "last_outcome": "http_error",
        "consecutive_failures": 36,
    }
    assert sources["google"]["last_polled_at"] is None


@pytest.mark.parametrize("app", [False], indirect=True)
async def test_polling_says_disabled_when_the_setting_is_off(
    incidents_path: tuple[Browser, str],
) -> None:
    browser, path = incidents_path
    response = await browser.get(path)
    assert response.status_code == 200, response.text
    assert response.json()["polling"] == "disabled"


async def test_the_route_counts_one_use(incidents_path: tuple[Browser, str]) -> None:
    browser, path = incidents_path
    used = metric_delta(FEATURE_USED, feature="provider_incidents")
    assert (await browser.get(path)).status_code == 200
    assert used.value == 1

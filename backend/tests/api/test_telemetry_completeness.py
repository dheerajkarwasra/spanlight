"""`GET /projects/{project_id}/telemetry-completeness`: stored spans against counted losses.

Failure modes pinned here: `100 %` for a window where nothing was stored and nothing lost (it is
unknown); a ratio that rounds up to `1.0000` while spans were lost; `lower_bound` set when only
one of the two overlapping sources counted something; stored spans counted from one half of the
rollup/raw split only; a loss total too large for a JSON number sent as one that a browser
rounds; another project's losses counted; a route that does not count its use.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

import pytest

from app.api.schemas.completeness import MAX_JSON_COUNT
from app.config import Settings
from app.core.observability import FEATURE_USED
from app.rollups.compute import HOUR, floor_hour
from tests.api.providers_support import (
    Call,
    SessionFactory,
    compute,
    pinned_app,
    pinned_now,
    seed_calls,
    seed_loss,
)
from tests.conftest import BrowserFactory
from tests.gateway_support import metric_delta
from tests.helpers import Browser, create_project, create_workspace

BIGINT_MAX = 2**63 - 1


@pytest.fixture
def now() -> datetime:
    return pinned_now()


@pytest.fixture
async def app(settings: Settings, now: datetime) -> AsyncIterator[Any]:
    async with pinned_app(settings, now) as application:
        yield application


@pytest.fixture
async def owner(browser_factory: BrowserFactory) -> tuple[Browser, uuid.UUID, str]:
    browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(browser)
    return browser, uuid.UUID(workspace.project_id), workspace.org_id


async def completeness(
    browser: Browser, project_id: uuid.UUID, start: datetime, end: datetime
) -> dict[str, Any]:
    response = await browser.get(
        f"/api/v1/projects/{project_id}/telemetry-completeness",
        params={"from": start.isoformat(), "to": end.isoformat()},
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_the_largest_count_json_carries_exactly_is_2_to_the_53_minus_1() -> None:
    assert MAX_JSON_COUNT == 2**53 - 1


async def test_nothing_stored_and_nothing_lost_is_unknown(
    owner: tuple[Browser, uuid.UUID, str], now: datetime
) -> None:
    browser, project_id, _ = owner
    body = await completeness(browser, project_id, now - HOUR, now)
    assert body == {"stored_spans": 0, "losses": [], "completeness": None, "lower_bound": False}


async def test_stored_spans_and_no_loss_is_complete(
    owner: tuple[Browser, uuid.UUID, str], session_factory: SessionFactory, now: datetime
) -> None:
    browser, project_id, _ = owner
    await seed_calls(session_factory, project_id, [Call(now - timedelta(minutes=5))] * 2)
    body = await completeness(browser, project_id, now - HOUR, now)
    assert body == {
        "stored_spans": 2,
        "losses": [],
        "completeness": "1.0000",
        "lower_bound": False,
    }


async def test_losses_and_nothing_stored_is_a_known_zero(
    owner: tuple[Browser, uuid.UUID, str], session_factory: SessionFactory, now: datetime
) -> None:
    browser, project_id, _ = owner
    await seed_loss(session_factory, project_id, floor_hour(now), "gateway", "overflow", 3)
    body = await completeness(browser, project_id, now - HOUR, now)
    assert body["stored_spans"] == 0
    assert body["losses"] == [{"source": "gateway", "reason": "overflow", "count": 3}]
    assert body["completeness"] == "0"


async def test_the_ratio_counts_stored_spans_across_the_rollup_split(
    owner: tuple[Browser, uuid.UUID, str], session_factory: SessionFactory, now: datetime
) -> None:
    browser, project_id, _ = owner
    hour = floor_hour(now)
    await seed_calls(session_factory, project_id, [Call(hour - 3 * HOUR)] * 2)
    await compute(session_factory, project_id, hour - 4 * HOUR, hour)
    await seed_calls(session_factory, project_id, [Call(now - timedelta(minutes=1))] * 4)
    await seed_loss(session_factory, project_id, hour - 2 * HOUR, "ingest", "validation", 1)
    await seed_loss(session_factory, project_id, hour, "sdk", "dropped", 2)

    body = await completeness(browser, project_id, hour - 3 * HOUR, now)

    assert body["stored_spans"] == 6
    assert body["losses"] == [
        {"source": "ingest", "reason": "validation", "count": 1},
        {"source": "sdk", "reason": "dropped", "count": 2},
    ]
    assert body["completeness"] == "0.6666"  # 6 / 9, rounded down
    assert body["lower_bound"] is False


@pytest.mark.parametrize(
    ("losses", "lower_bound"),
    [
        ([("sdk", "failed"), ("ingest", "too_old")], True),
        ([("sdk", "failed")], False),
        ([("ingest", "too_old"), ("sdk", "dropped")], False),
    ],
)
async def test_lower_bound_needs_both_sdk_failed_and_ingest_rejections(
    owner: tuple[Browser, uuid.UUID, str],
    session_factory: SessionFactory,
    now: datetime,
    losses: list[tuple[str, str]],
    lower_bound: bool,
) -> None:
    browser, project_id, _ = owner
    await seed_calls(session_factory, project_id, [Call(now - timedelta(minutes=5))])
    for source, reason in losses:
        await seed_loss(session_factory, project_id, floor_hour(now), source, reason, 1)

    body = await completeness(browser, project_id, now - HOUR, now)

    assert body["lower_bound"] is lower_bound


async def test_counts_beyond_what_json_carries_exactly_are_clamped(
    owner: tuple[Browser, uuid.UUID, str], session_factory: SessionFactory, now: datetime
) -> None:
    browser, project_id, _ = owner
    await seed_calls(session_factory, project_id, [Call(now - timedelta(minutes=5))])
    for hours_ago in (0, 1):
        bucket = floor_hour(now) - hours_ago * HOUR
        await seed_loss(session_factory, project_id, bucket, "gateway", "error", BIGINT_MAX)

    body = await completeness(browser, project_id, now - 2 * HOUR, now)

    assert body["losses"] == [{"source": "gateway", "reason": "error", "count": MAX_JSON_COUNT}]
    assert body["completeness"] == "0.0000"  # 1 stored against far more lost, never "0"


async def test_another_projects_losses_and_spans_do_not_count(
    owner: tuple[Browser, uuid.UUID, str], session_factory: SessionFactory, now: datetime
) -> None:
    browser, project_id, org_id = owner
    other = uuid.UUID(await create_project(browser, org_id, "Other"))
    await seed_calls(session_factory, other, [Call(now - timedelta(minutes=5))])
    await seed_loss(session_factory, other, floor_hour(now), "gateway", "overflow", 5)

    body = await completeness(browser, project_id, now - HOUR, now)

    assert body == {"stored_spans": 0, "losses": [], "completeness": None, "lower_bound": False}


async def test_the_route_counts_one_use(
    owner: tuple[Browser, uuid.UUID, str], now: datetime
) -> None:
    browser, project_id, _ = owner
    used = metric_delta(FEATURE_USED, feature="telemetry_completeness")
    await completeness(browser, project_id, now - HOUR, now)
    assert used.value == 1

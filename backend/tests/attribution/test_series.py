"""`GET /projects/{project_id}/attribution/series`: the Spend chart's one series.

Same fixture, sources and key rule as the list (`tests.attribution.test_api`): the raw path is
hourly, the rollup path daily, both gap-filled, and the points add up to the list's totals.
"""

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.attribution.aggregate_sql import GroupKind
from tests.attribution.support import DAYS, seed_attribution_fixture
from tests.attribution.test_api import World, promote, seed_rollups, window
from tests.helpers import Browser, create_workspace
from tests.jobs.bypass_support import SessionFactory

D0, D1, D2 = DAYS
D3 = D2 + timedelta(days=1)


@pytest.fixture
async def world(browser_factory: Any, session_factory: SessionFactory) -> World:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    await seed_attribution_fixture(session_factory, uuid.UUID(workspace.project_id))
    return World(owner, workspace, session_factory)


def series_path(world: World) -> str:
    return f"{world.path}/series"


async def read_series(world: World, **params: Any) -> dict[str, Any]:
    response = await world.owner.get(series_path(world), params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def costs(body: dict[str, Any]) -> list[str | None]:
    return [point["cost_usd"] for point in body["points"]]


def cost_sum(body: dict[str, Any]) -> Decimal:
    return sum((Decimal(cost) for cost in costs(body) if cost is not None), Decimal(0))


@pytest.mark.parametrize(
    ("source", "params"), [("raw", window(D1, D2)), ("rollup", window(D0, D3))]
)
async def test_series_total_matches_list_totals(
    world: World, source: str, params: dict[str, str]
) -> None:
    await seed_rollups(world, "trace_name", None)
    listed = await world.owner.get(world.path, params={"group_by": "trace_name", **params})
    totals = listed.json()["totals"]
    body = await read_series(world, group_by="trace_name", **params)

    assert body["source"] == source
    assert body["window"] == listed.json()["window"]
    assert cost_sum(body) == Decimal(totals["cost_usd"])
    assert sum(point["unpriced_calls"] for point in body["points"]) == totals["unpriced_calls"]


@pytest.mark.parametrize(
    ("kind", "key", "value", "daily"),
    [
        ("trace_name", None, "answer", ["0.65000000", "0", None]),  # t6 alone on D2: unpriced
        ("user", None, "u1", ["0.15000000", "0.43000000", "0.70000000"]),
        ("release", None, "r2", ["0", "0.43000000", "0.70000000"]),
        ("metadata", "customer", "acme", ["0.65000000", "0", "0"]),
    ],
)
async def test_series_value_filters_one_group(
    world: World, kind: GroupKind, key: str | None, value: str, daily: list[str | None]
) -> None:
    """The rollup path per day, and the raw path's hours of D0 adding up to that day."""
    if key is not None:
        await promote(world, [key])
    await seed_rollups(world, kind, key)
    params = {"group_by": kind, "value": value, **({"key": key} if key else {})}

    rollup = await read_series(world, **params, **window(D0, D3))
    assert costs(rollup) == daily

    raw = await read_series(world, **params, **window(D0, D1))
    assert raw["source"] == "raw"
    assert cost_sum(raw) == Decimal(daily[0] or 0)


async def test_series_raw_is_hourly_gap_filled(world: World) -> None:
    aligned = await read_series(world, group_by="trace_name", **window(D0, D1))
    assert (aligned["bucket"], aligned["source"]) == ("hour", "raw")
    assert len(aligned["points"]) == 24
    assert aligned["points"][0]["bucket_start"] == f"{D0}T00:00:00Z"
    by_hour = {point["bucket_start"]: point["cost_usd"] for point in aligned["points"]}
    assert by_hour[f"{D0}T10:00:00Z"] == "0.15000000"  # t1
    assert by_hour[f"{D0}T23:00:00Z"] == "0.50000000"  # t2, its D1 span included
    assert by_hour[f"{D0}T05:00:00Z"] == "0"  # no traces: a known zero
    assert aligned["points"][5]["unpriced_calls"] == 0

    shifted = {"from": f"{D0}T00:30:00Z", "to": f"{D1}T00:30:00Z"}
    body = await read_series(world, group_by="trace_name", **shifted)
    assert len(body["points"]) == 25
    assert body["points"][0]["bucket_start"] == f"{D0}T00:00:00Z"
    assert body["points"][-1]["bucket_start"] == f"{D1}T00:00:00Z"


async def test_series_rollup_is_daily(world: World) -> None:
    await seed_rollups(world, "user", None)
    body = await read_series(
        world, group_by="user", **{"from": f"{D0}T12:00:00Z", "to": f"{D2}T06:00:00Z"}
    )
    assert (body["bucket"], body["source"]) == ("day", "rollup")
    assert body["window"] == {"from": f"{D0}T00:00:00Z", "to": f"{D3}T00:00:00Z"}
    starts = [point["bucket_start"] for point in body["points"]]
    assert starts == [f"{day}T00:00:00Z" for day in DAYS]
    assert costs(body) == ["0.65000000", "0.44000000", "0.70000000"]
    assert [point["unpriced_calls"] for point in body["points"]] == [0, 1, 1]


async def test_series_unpriced_bucket_cost_is_null_when_no_priced_calls(world: World) -> None:
    body = await read_series(world, group_by="trace_name", **window(D2, D3))
    by_hour = {point["bucket_start"]: point for point in body["points"]}
    assert by_hour[f"{D2}T09:00:00Z"]["cost_usd"] == "0.70000000"
    assert by_hour[f"{D2}T11:00:00Z"] == {
        "bucket_start": f"{D2}T11:00:00Z",
        "cost_usd": None,
        "unpriced_calls": 1,
    }


async def test_series_unpromoted_key_is_refused_as_the_list(world: World) -> None:
    """Over 24 hours on a key that is not promoted: the list's `422 KEY_NOT_PROMOTED`."""
    longer = {"from": f"{D1}T00:00:00Z", "to": f"{D2}T00:00:01Z"}
    params = {"group_by": "metadata", "key": "customer", "value": "acme", **longer}
    refused = await world.owner.get(series_path(world), params=params)
    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "KEY_NOT_PROMOTED"

    missing = await world.owner.get(series_path(world), params={"group_by": "metadata"})
    assert missing.status_code == 422
    assert [error["field"] for error in missing.json()["errors"]] == ["key"]


async def test_series_cross_tenant_reads_nothing(world: World, browser_factory: Any) -> None:
    """The other project's 5.00 trace at D1 10:00 never shows; a non-member gets 404."""
    body = await read_series(world, group_by="trace_name", **window(D1, D2))
    by_hour = {point["bucket_start"]: point["cost_usd"] for point in body["points"]}
    assert by_hour[f"{D1}T10:00:00Z"] == "0"

    outsider: Browser = await browser_factory("outsider@example.com")
    await create_workspace(outsider, org_name="Elsewhere")
    response = await outsider.get(
        series_path(world), params={"group_by": "trace_name", **window(D1, D2)}
    )
    assert response.status_code == 404

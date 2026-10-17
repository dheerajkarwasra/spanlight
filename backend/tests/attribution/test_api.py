"""`GET /projects/{project_id}/attribution`: the source rule, the envelope, sorting and tenancy.

The traces are the three-day fixture of `tests.attribution.support`, seeded into a workspace made
through the API. The rollup rows a test needs are written by `seed_rollups`, which runs the same
`grouped` CTE the refresh job uses (by day, every environment) and inserts its rows, so the
rollup path is tested without the job itself.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from app.attribution.aggregate_sql import GroupKind, grouped_cte
from app.core.observability import FEATURE_USED
from app.db.rls import bypass_rls
from tests.attribution.support import DAYS, WINDOW_END, WINDOW_START, seed_attribution_fixture
from tests.gateway_support import metric_delta
from tests.helpers import (
    Browser,
    Workspace,
    bearer,
    create_api_key,
    create_workspace,
    join_with_role,
)
from tests.jobs.bypass_support import SessionFactory

D0, D1, D2 = DAYS
D3 = D2 + timedelta(days=1)
FIGURES = (
    "traces",
    "llm_calls",
    "errors",
    "unpriced_calls",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "retries",
    "retry_cost_usd",
)

# What the fixture adds up to, by group, for the assertions below (see the support module).
T4 = {
    "traces": 1,
    "llm_calls": 2,
    "errors": 0,
    "unpriced_calls": 1,
    "input_tokens": 480,
    "output_tokens": 45,
    "cost_usd": "0.43000000",
    "retries": None,
    "retry_cost_usd": None,
}
T3 = {
    "traces": 1,
    "llm_calls": 0,
    "errors": 0,
    "unpriced_calls": 0,
    "input_tokens": 7,
    "output_tokens": 3,
    "cost_usd": "0.01000000",
    "retries": None,
    "retry_cost_usd": None,
}
ACME = {
    "traces": 2,
    "llm_calls": 4,
    "errors": 1,
    "unpriced_calls": 0,
    "input_tokens": 610,
    "output_tokens": 70,
    "cost_usd": "0.65000000",
    "retries": 1,
    "retry_cost_usd": "0.05000000",
}
GLOBEX = {
    "traces": 2,
    "llm_calls": 3,
    "errors": 0,
    "unpriced_calls": 1,
    "input_tokens": 1480,
    "output_tokens": 145,
    "cost_usd": "1.13000000",
    "retries": 2,
    "retry_cost_usd": None,  # t5 has retries and no retry cost: unknown
}

_INSERT_ROLLUPS = """
    WITH {grouped}
    INSERT INTO attribution_rollups_daily
        (project_id, day, environment, group_kind, group_key, bucket, group_value, traces,
         llm_calls, errors, unpriced_calls, input_tokens, output_tokens, cost_usd, retries,
         retry_cost_usd)
    SELECT CAST(:project_id AS uuid), day, environment, CAST(:kind AS text),
           CAST(:key AS text), CASE WHEN group_value IS NULL THEN 'none' ELSE 'value' END,
           group_value, traces, llm_calls, errors, unpriced_calls, input_tokens, output_tokens,
           cost_usd, retries, retry_cost_usd
    FROM grouped
"""
_INSERT_OTHER = text(
    """
    INSERT INTO attribution_rollups_daily
        (project_id, day, environment, group_kind, group_key, bucket, group_value, traces,
         llm_calls, errors, unpriced_calls, input_tokens, output_tokens, cost_usd, folded_values)
    VALUES (:project_id, :day, 'prod', 'metadata', 'customer', 'other', NULL, :traces, :traces,
            0, 0, 10, 5, :cost_usd, :folded_values)
    """
)


@dataclass(frozen=True)
class World:
    owner: Browser
    workspace: Workspace
    session_factory: SessionFactory

    @property
    def project_id(self) -> uuid.UUID:
        return uuid.UUID(self.workspace.project_id)

    @property
    def path(self) -> str:
        return f"/api/v1/projects/{self.workspace.project_id}/attribution"


@pytest.fixture
async def world(browser_factory: Any, session_factory: SessionFactory) -> World:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    await seed_attribution_fixture(session_factory, uuid.UUID(workspace.project_id))
    return World(owner, workspace, session_factory)


async def seed_rollups(
    world: World,
    kind: GroupKind,
    key: str | None,
    start: datetime = WINDOW_START,
    end: datetime = WINDOW_END,
) -> None:
    """Rollup rows for the traces started in `[start, end)`, as the refresh would write them."""
    sql = _INSERT_ROLLUPS.format(grouped=grouped_cte(kind, by_day=True, with_environment=False))
    params = {"project_id": world.project_id, "kind": kind, "key": key, "start": start, "end": end}
    async with world.session_factory() as db:
        await bypass_rls(db)
        await db.execute(text(sql), params)
        await db.commit()


async def promote(world: World, keys: list[str]) -> None:
    response = await world.owner.patch(
        f"/api/v1/projects/{world.workspace.project_id}",
        json={"promoted_metadata_keys": keys},
    )
    assert response.status_code == 200, response.text


def window(start: date, end: date) -> dict[str, str]:
    """`from`/`to` at midnight UTC of two days."""
    return {"from": f"{start}T00:00:00Z", "to": f"{end}T00:00:00Z"}


async def read(world: World, **params: Any) -> dict[str, Any]:
    response = await world.owner.get(world.path, params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def figures(row: dict[str, Any]) -> dict[str, Any]:
    return {name: row[name] for name in FIGURES}


def values(body: dict[str, Any]) -> list[str]:
    return [item["value"] for item in body["items"]]


async def test_raw_and_rollup_agree_on_a_fully_covered_day(world: World) -> None:
    """The first day read raw, and read from its rollup rows alone, give the same figures."""
    await promote(world, ["customer"])
    await seed_rollups(world, "metadata", "customer", end=WINDOW_START + timedelta(days=1))
    raw = await read(world, group_by="metadata", key="customer", **window(D0, D1))
    # One second past 24 hours: the rollup path, widened to [D0, D2), and D1 has no rows.
    longer = {"from": f"{D0}T00:00:00Z", "to": f"{D1}T00:00:01Z"}
    rollup = await read(world, group_by="metadata", key="customer", **longer)

    assert (raw["source"], raw["updated_within_minutes"]) == ("raw", 0)
    assert (rollup["source"], rollup["updated_within_minutes"]) == ("rollup", 15)
    assert raw["window"] == {"from": f"{D0}T00:00:00Z", "to": f"{D1}T00:00:00Z"}
    assert rollup["window"] == {"from": f"{D0}T00:00:00Z", "to": f"{D2}T00:00:00Z"}
    for name in ("items", "none", "other", "totals"):
        assert raw[name] == rollup[name], name
    assert values(raw) == ["acme"]
    assert figures(raw["items"][0]) == ACME
    assert figures(raw["totals"]) == ACME
    assert raw["none"] is None and raw["other"] is None


async def test_rollup_window_with_offset_is_widened_to_utc_days_and_echoed(world: World) -> None:
    """A window given at +05:30 is widened to the UTC days it touches, and echoed in UTC."""
    await seed_rollups(world, "trace_name", None)
    body = await read(
        world,
        group_by="trace_name",
        **{"from": "2026-09-02T05:30:00+05:30", "to": "2026-09-03T12:00:00+05:30"},
    )

    assert body["source"] == "rollup"
    assert body["window"] == {"from": "2026-09-02T00:00:00Z", "to": "2026-09-04T00:00:00Z"}
    # D1 and D2: t4 and t5 are `summarize`, t6 is `answer`, t3 has no name.
    assert values(body) == ["summarize", "answer"]
    assert body["items"][0]["traces"] == 2
    assert body["none"]["traces"] == 1
    assert body["totals"]["traces"] == 4


async def test_non_promoted_key_over_24h_is_key_not_promoted_and_fine_at_24h(
    world: World,
) -> None:
    exactly = await read(world, group_by="metadata", key="customer", **window(D1, D2))
    assert exactly["source"] == "raw"
    assert values(exactly) == ["globex"]

    longer = {"from": f"{D1}T00:00:00Z", "to": f"{D2}T00:00:01Z"}
    refused = await world.owner.get(
        world.path, params={"group_by": "metadata", "key": "customer", **longer}
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "KEY_NOT_PROMOTED"
    assert refused.json()["detail"] == (
        "Promote `customer` in project settings to group over more than 24 hours"
    )

    await promote(world, ["customer"])
    await seed_rollups(world, "metadata", "customer")
    allowed = await read(world, group_by="metadata", key="customer", **longer)
    assert allowed["source"] == "rollup"
    assert values(allowed) == ["globex"]


async def walk(world: World, **params: Any) -> list[list[str]]:
    """The values of every page, following `next_cursor` to the end."""
    pages: list[list[str]] = []
    cursor = None
    for _ in range(10):
        body = await read(world, **params, **({"cursor": cursor} if cursor else {}))
        pages.append(values(body))
        cursor = body["next_cursor"]
        if cursor is None:
            break
    return pages


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("cost", ["y", "x", "z"]),  # z is unpriced: unknown last
        ("calls", ["x", "y", "z"]),  # x and y tie at 2 calls: value ascending
        ("errors", ["x", "z", "y"]),
        ("retry_cost", ["x", "z", "y"]),  # y's traces predate the retry columns: unknown
    ],
)
async def test_sort_and_cursor_on_each_sort_key_including_null_costs(
    world: World, sort: str, expected: list[str]
) -> None:
    await promote(world, ["a.b-c"])
    await seed_rollups(world, "metadata", "a.b-c")
    params = {"group_by": "metadata", "key": "a.b-c", "sort": sort, **window(D0, D3)}

    whole = await read(world, **params)
    assert values(whole) == expected
    assert whole["next_cursor"] is None

    assert await walk(world, limit=1, **params) == [[value] for value in expected]


async def test_forged_cursor_is_422(world: World) -> None:
    response = await world.owner.get(
        world.path,
        params={"group_by": "trace_name", "cursor": "bm90IGEgY3Vyc29y", **window(D1, D2)},
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["errors"] == [{"field": "cursor", "message": "Invalid cursor"}]


async def test_share_of_cost_rounds_to_four_places_and_is_null_without_cost(
    world: World,
) -> None:
    await promote(world, ["customer", "a.b-c"])
    await seed_rollups(world, "metadata", "customer")
    await seed_rollups(world, "metadata", "a.b-c")

    customers = await read(world, group_by="metadata", key="customer", **window(D0, D3))
    assert customers["totals"]["cost_usd"] == "1.79000000"
    shares = {item["value"]: item["share_of_cost"] for item in customers["items"]}
    assert shares == {"globex": 0.6313, "acme": 0.3631}  # 1.13 / 1.79 and 0.65 / 1.79

    keys = await read(world, group_by="metadata", key="a.b-c", **window(D0, D3))
    unpriced = [item for item in keys["items"] if item["value"] == "z"]
    assert unpriced[0]["cost_usd"] is None
    assert unpriced[0]["share_of_cost"] is None


async def test_none_other_and_totals_envelope(world: World) -> None:
    """Raw: `none` is the traces without the key and `other` is always null. Rollup: `other`
    is the fold, summed across days with its `folded_values`."""
    raw = await read(world, group_by="metadata", key="customer", **window(D1, D2))
    assert values(raw) == ["globex"]
    assert figures(raw["items"][0]) == T4
    assert figures(raw["none"]) == T3
    assert raw["none"]["folded_values"] is None
    assert raw["other"] is None
    assert figures(raw["totals"]) == {
        "traces": 2,
        "llm_calls": 2,
        "errors": 0,
        "unpriced_calls": 1,
        "input_tokens": 487,
        "output_tokens": 48,
        "cost_usd": "0.44000000",
        "retries": None,
        "retry_cost_usd": None,
    }

    await promote(world, ["customer"])
    await seed_rollups(world, "metadata", "customer")
    async with world.session_factory() as db:
        await bypass_rls(db)
        for day, traces, cost, folded in ((D1, 3, "0.30", 3), (D2, 2, "0.20", 2)):
            params = {"traces": traces, "cost_usd": cost, "folded_values": folded}
            await db.execute(_INSERT_OTHER, {**params, "project_id": world.project_id, "day": day})
        await db.commit()
    rollup = await read(world, group_by="metadata", key="customer", **window(D0, D3))
    assert values(rollup) == ["globex", "acme"]
    assert figures(rollup["items"][0]) == GLOBEX
    assert figures(rollup["items"][1]) == ACME
    assert rollup["other"]["folded_values"] == 5
    assert rollup["other"]["traces"] == 5
    assert rollup["other"]["cost_usd"] == "0.50000000"
    assert rollup["none"]["traces"] == 2
    assert rollup["totals"]["traces"] == 11
    assert rollup["totals"]["cost_usd"] == "2.29000000"
    assert rollup["totals"]["retry_cost_usd"] is None


async def test_environment_filter(world: World) -> None:
    raw = await read(
        world, group_by="metadata", key="customer", environment="staging", **window(D1, D2)
    )
    assert values(raw) == ["globex"]
    assert raw["none"] is None  # t3 has no environment
    assert figures(raw["totals"]) == T4

    await promote(world, ["customer"])
    await seed_rollups(world, "metadata", "customer")
    rollup = await read(
        world, group_by="metadata", key="customer", environment="prod", **window(D0, D3)
    )
    assert values(rollup) == ["globex", "acme"]
    assert figures(rollup["items"][1]) == ACME
    assert rollup["items"][0]["traces"] == 1  # t5 only: t4 is staging
    assert rollup["items"][0]["cost_usd"] == "0.70000000"
    assert rollup["none"]["traces"] == 1  # t6 only
    assert rollup["totals"]["traces"] == 4


async def test_empty_environment_filters_both_paths(world: World) -> None:
    """`environment=` names the empty environment (no trace has it), as on `/traces`: nothing on
    the raw path and nothing on the rollup path either, never the unfiltered rows."""
    await seed_rollups(world, "trace_name", None)
    for params in (window(D1, D2), window(D0, D3)):
        body = await read(world, group_by="trace_name", environment="", **params)
        assert (body["items"], body["none"], body["totals"]["traces"]) == ([], None, 0)


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"group_by": "metadata"}, "key"),
        ({"group_by": "trace_name", "key": "customer"}, "key"),
        ({"group_by": "metadata", "key": "Customer"}, "key"),
        ({"group_by": "team"}, "group_by"),
    ],
)
async def test_key_required_for_metadata_and_rejected_for_other_kinds(
    world: World, params: dict[str, str], field: str
) -> None:
    response = await world.owner.get(world.path, params={**params, **window(D1, D2)})
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert [error["field"] for error in response.json()["errors"]] == [field]


async def test_non_member_404_viewer_200_api_key_with_traces_read_200(
    world: World, browser_factory: Any, client: httpx.AsyncClient
) -> None:
    params = {"group_by": "trace_name", **window(D1, D2)}

    outsider: Browser = await browser_factory("outsider@example.com")
    await create_workspace(outsider, org_name="Elsewhere")
    assert (await outsider.get(world.path, params=params)).status_code == 404

    viewer: Browser = await browser_factory("viewer@example.com")
    await join_with_role(world.owner, viewer, world.workspace.org_id, "viewer")
    seen = await viewer.get(world.path, params=params)
    assert seen.status_code == 200, seen.text
    assert values(seen.json()) == ["summarize"]

    key = await create_api_key(world.owner, world.workspace.project_id, scopes=["traces:read"])
    by_key = await client.get(world.path, params=params, headers=bearer(key["secret"]))
    assert by_key.status_code == 200, by_key.text
    assert by_key.json() == seen.json()

    ingest_only = await client.get(
        world.path, params=params, headers=bearer(world.workspace.api_key)
    )
    assert ingest_only.status_code == 403
    assert ingest_only.json()["code"] == "KEY_SCOPE"


async def test_attribution_view_feature_counter(world: World) -> None:
    views = metric_delta(FEATURE_USED, feature="attribution_view")
    await read(world, group_by="user", **window(D1, D2))
    assert views.value == 1
    refused = await world.owner.get(world.path, params={"group_by": "metadata"})
    assert refused.status_code == 422
    assert views.value == 1

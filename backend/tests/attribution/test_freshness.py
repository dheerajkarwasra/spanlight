"""Attribution freshness: `computed_at` and `pending_traces` on the rollup source (spec §6.1).

Failure mode: the page read "Updated" over rollup rows that never existed, or that predate the
traces it was asked about, while Overview showed those traces; the reader could not tell the
page was behind. The traces are the three-day fixture of `tests.attribution.support`.
"""

import uuid
from datetime import date, datetime, timedelta
from typing import Any

import pytest

from tests.attribution.support import DAYS, at, seed_attribution_fixture
from tests.attribution.test_api import World, read, seed_rollups, window
from tests.helpers import Browser, create_workspace
from tests.jobs.bypass_support import SessionFactory, execute

D0, D1, D2 = DAYS
D3 = D2 + timedelta(days=1)

_SET_COMPUTED_AT = """
    UPDATE attribution_rollups_daily SET computed_at = :computed_at
    WHERE project_id = :project_id AND day >= :first_day
"""


@pytest.fixture
async def fresh_world(browser_factory: Any, session_factory: SessionFactory) -> World:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    await seed_attribution_fixture(session_factory, uuid.UUID(workspace.project_id))
    return World(owner, workspace, session_factory)


async def _set_computed_at(world: World, computed_at: datetime, first_day: date = D0) -> None:
    params = {"project_id": world.project_id, "computed_at": computed_at, "first_day": first_day}
    await execute(world.session_factory, _SET_COMPUTED_AT, params)


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


async def test_rollup_source_reports_latest_computed_at(fresh_world: World) -> None:
    await seed_rollups(fresh_world, "trace_name", None)
    await _set_computed_at(fresh_world, at(4, 9))
    await _set_computed_at(fresh_world, at(5, 9), first_day=D1)

    body = await read(fresh_world, group_by="trace_name", **window(D0, D3))
    assert body["source"] == "rollup"
    assert body["computed_at"] == _iso(at(5, 9))
    assert body["pending_traces"] == 0

    raw = await read(fresh_world, group_by="trace_name", **window(D2, D3))
    assert raw["source"] == "raw"
    assert (raw["computed_at"], raw["pending_traces"]) == (None, None)


async def test_pending_traces_counts_traces_started_after_computed_at(fresh_world: World) -> None:
    await seed_rollups(fresh_world, "trace_name", None)
    await _set_computed_at(fresh_world, at(1, 12, 30))

    body = await read(fresh_world, group_by="trace_name", **window(D0, D3))
    # Per day: t4 (D1 13:00, staging) is after D1's recompute, and D2's rows were computed before
    # t5 and t6 (prod) started; t1 to t3 were in. The trace at the window's end is outside.
    assert (body["computed_at"], body["pending_traces"]) == (_iso(at(1, 12, 30)), 3)

    prod = await read(fresh_world, group_by="trace_name", environment="prod", **window(D0, D3))
    assert prod["pending_traces"] == 2


async def test_pending_traces_when_rollup_never_ran_counts_window(fresh_world: World) -> None:
    body = await read(fresh_world, group_by="trace_name", **window(D0, D3))
    assert body["source"] == "rollup"
    assert (body["computed_at"], body["pending_traces"]) == (None, 6)
    assert body["totals"]["traces"] == 0

    prod = await read(fresh_world, group_by="trace_name", environment="prod", **window(D0, D3))
    assert (prod["computed_at"], prod["pending_traces"]) == (None, 4)


async def test_pending_ignores_traces_already_in_older_days_rollup(fresh_world: World) -> None:
    """A 7-day window: each old day was recomputed after it ended, so its traces are in; today's
    rows were computed between t5 and t6, so only t6 is pending."""
    first = D0 - timedelta(days=4)
    await seed_rollups(fresh_world, "trace_name", None, start=at(-1, 0))
    await _set_computed_at(fresh_world, at(0, 0, 5), first_day=first)
    await _set_computed_at(fresh_world, at(1, 0, 5), first_day=D0)
    await _set_computed_at(fresh_world, at(2, 0, 5), first_day=D1)
    await _set_computed_at(fresh_world, at(2, 10), first_day=D2)

    body = await read(fresh_world, group_by="trace_name", **window(first, D3))
    assert body["source"] == "rollup"
    assert (body["computed_at"], body["pending_traces"]) == (_iso(at(2, 10)), 1)

    prod = await read(fresh_world, group_by="trace_name", environment="prod", **window(first, D3))
    assert prod["pending_traces"] == 1

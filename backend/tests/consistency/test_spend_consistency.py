"""Overview, the traces list and attribution report the same spend for the same window (§6.1).

Failure mode: three pages of one project disagree on what the same hours cost, or on how many
calls are unpriced, so a reader trusts none of them. The traces sit fully inside one UTC day
(every span starts on the day its trace does), so span-time and trace-start windows agree; two
environments, an unpriced call, a priced usage rollup and an unpriced usage rollup are in it.
Attribution is read raw (24 hours) and from the rollup (48 hours, after a refresh).
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.attribution.refresh import refresh_attribution
from app.db.rls import bind_project, bypass_rls
from tests.attribution.support import SpanSeed, TraceSeed, seed_traces
from tests.attribution.test_unpriced_excludes_usage_rollup import nest_and_recompute
from tests.helpers import Browser, create_workspace
from tests.jobs.bypass_support import SessionFactory

DAY = date(2026, 9, 10)
START = datetime.combine(DAY, datetime.min.time(), UTC)


def _llm(hour: int, second: int, cost: str | None) -> SpanSeed:
    started = START + timedelta(hours=hour, seconds=second)
    return SpanSeed("llm", started, None if cost is None else Decimal(cost), 10, 2)


def _trace(hour: int, environment: str, *spans: SpanSeed) -> TraceSeed:
    return TraceSeed(START + timedelta(hours=hour), spans, environment=environment, name="answer")


TRACES = {
    "plain": _trace(1, "prod", _llm(1, 0, "0.10")),
    # An unpriced outer span over a priced call: a usage rollup, neither summed nor unpriced.
    "rollup_unpriced": _trace(2, "prod", _llm(2, 0, None), _llm(2, 1, "0.20")),
    "unpriced": _trace(3, "staging", _llm(3, 0, None), _llm(3, 1, "0.30")),
    # A priced outer span over a priced call: a usage rollup whose cost is never summed.
    "rollup_priced": _trace(4, "staging", _llm(4, 0, "0.25"), _llm(4, 1, "0.05")),
}


async def _get(owner: Browser, path: str, **params: Any) -> Any:
    response = await owner.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _money(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


async def _figures(owner: Browser, project: str, end: datetime, env: str | None) -> list[Any]:
    """(overview spend, traces-list spend, attribution spend, overview unpriced, attribution
    unpriced, attribution source) for `[START, end)`."""
    window = {"from": START.isoformat(), "to": end.isoformat()}
    filters = {**window, **({} if env is None else {"environment": env})}
    base = f"/api/v1/projects/{project}"
    overview = (await _get(owner, f"{base}/metrics/overview", **filters))["current"]
    traces = await _get(owner, f"{base}/traces", limit=100, **filters)
    assert traces["next_cursor"] is None
    attribution = await _get(owner, f"{base}/attribution", group_by="trace_name", **filters)
    listed = [_money(item["cost_usd"]) for item in traces["items"]]
    return [
        _money(overview["cost_usd"]),
        sum((cost for cost in listed if cost is not None), Decimal(0)),
        _money(attribution["totals"]["cost_usd"]),
        overview["unpriced_calls"],
        attribution["totals"]["unpriced_calls"],
        attribution["source"],
    ]


async def test_overview_traces_and_attribution_agree_on_spend(
    browser_factory: Any, session_factory: SessionFactory
) -> None:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    project_id = uuid.UUID(workspace.project_id)
    async with session_factory() as db:
        await bypass_rls(db)
        ids = await seed_traces(db, project_id, TRACES)
        for label in ("rollup_unpriced", "rollup_priced"):
            await nest_and_recompute(db, project_id, ids[label])
        await db.commit()

    expected = {None: (Decimal("0.65"), 1), "prod": (Decimal("0.30"), 0)}
    raw_end, rollup_end = START + timedelta(days=1), START + timedelta(days=2)
    for env, (spend, unpriced) in expected.items():
        raw = await _figures(owner, workspace.project_id, raw_end, env)
        assert raw == [spend, spend, spend, unpriced, unpriced, "raw"], env

    async with session_factory() as db, db.begin():
        await bind_project(db, project_id)
        await refresh_attribution(db, project_id, [], [DAY])
    for env, (spend, unpriced) in expected.items():
        rollup = await _figures(owner, workspace.project_id, rollup_end, env)
        assert rollup == [spend, spend, spend, unpriced, unpriced, "rollup"], env

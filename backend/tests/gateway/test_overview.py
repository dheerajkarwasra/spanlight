"""The gateway overview's previous window, upstream p95, rate-limit count and requests series.

Spans are ingested natively and then made the gateway's: a key id on the row, the gateway's
attributes and the rate-limit kind written directly, as the gateway's recorder would store them.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Span
from app.db.rls import bypass_rls
from tests.helpers import Browser, Workspace, bearer, create_workspace, new_trace_id, span

SessionFactory = async_sessionmaker[AsyncSession]

# Every window ends here; a whole minute so bucket starts compare exactly.
END = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=1)


@pytest.fixture
async def workspace(browser_factory: Any) -> Workspace:
    owner: Browser = await browser_factory("owner@example.com")
    return await create_workspace(owner)


async def _gateway_span(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: SessionFactory,
    start: datetime,
    *,
    status: str = "ok",
    duration_ms: float = 100.0,
    environment: str = "prod",
    rate_limit_kind: str | None = None,
    **attributes: Any,
) -> None:
    """One gateway call at `start`; `attributes` use `_` for `.` after `spanlight.gateway.`."""
    span_id = uuid.uuid4().hex[:16]
    body = span(
        trace_id=new_trace_id(),
        span_id=span_id,
        start=start,
        duration_ms=duration_ms,
        status=status,
        trace={"name": "answer", "environment": environment},
    )
    response = await client.post(
        "/v1/traces", json={"spans": [body]}, headers=bearer(workspace.api_key)
    )
    assert response.json()["accepted"] == 1, response.text
    stored = {
        name if name == "spanlight.cache" else f"spanlight.gateway.{name}": value
        for name, value in attributes.items()
    }
    async with session_factory() as db:
        await bypass_rls(db)
        await db.execute(
            update(Span)
            .where(Span.span_id == span_id)
            .values(source_key_id=uuid.uuid4(), attributes=stored, rate_limit_kind=rate_limit_kind)
        )
        await db.commit()


async def _overview(
    workspace: Workspace, start: datetime, end: datetime = END, **params: str
) -> dict[str, Any]:
    response = await workspace.owner.get(
        f"/api/v1/projects/{workspace.project_id}/gateway/overview",
        params={"from": start.isoformat(), "to": end.isoformat(), **params},
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _points(body: dict[str, Any]) -> list[tuple[datetime, int, int]]:
    return [
        (datetime.fromisoformat(point["bucket_start"]), point["requests"], point["errors"])
        for point in body["series"]["points"]
    ]


def _on_the_clock(moment: datetime, step: timedelta) -> datetime:
    """The start of the clock-aligned bucket of `step` holding `moment` (whole hours, :05, ...)."""
    return moment - (moment - datetime(2001, 1, 1, tzinfo=UTC)) % step


def _expected(
    start: datetime, step: timedelta, calls: dict[datetime, tuple[int, int]]
) -> list[tuple[datetime, int, int]]:
    """Each clock-aligned bucket from the one holding `start` up to `END`; 0 unless in `calls`."""
    points = []
    current = _on_the_clock(start, step)
    while current < END:
        points.append((current, *calls.get(current, (0, 0))))
        current += step
    return points


async def test_overview_previous_window_counts(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    start = END - timedelta(hours=2)
    await _gateway_span(client, workspace, session_factory, END - timedelta(minutes=30))
    await _gateway_span(
        client, workspace, session_factory, END - timedelta(minutes=20), status="error"
    )
    # The window of equal length right before: [END - 4 h, END - 2 h).
    await _gateway_span(
        client,
        workspace,
        session_factory,
        END - timedelta(hours=3),
        rate_limit_kind="requests",
        overhead_ms=12.5,
        fallbacks=1,
        attempts=2,
        **{"spanlight.cache": "hit"},
    )
    await _gateway_span(client, workspace, session_factory, END - timedelta(hours=5))

    body = await _overview(workspace, start)

    assert (body["requests"], body["errors"]) == (2, 1)
    assert body["previous"] == {
        "requests": 1,
        "errors": 0,
        "error_rate": 0.0,
        "p95_overhead_ms": 12.5,
        "cache_hit_rate": 1.0,
        "fallbacks": 1,
        "rate_limited": 1,
    }


async def test_overview_upstream_excludes_spans_without_overhead(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    at = END - timedelta(minutes=10)
    await _gateway_span(
        client, workspace, session_factory, at, duration_ms=300, overhead_ms=20, attempts=1
    )
    # No overhead recorded: not measured, so not counted.
    await _gateway_span(client, workspace, session_factory, at, duration_ms=5000, attempts=1)
    # No provider attempt (a cache hit or a refusal): the whole duration is the gateway's.
    await _gateway_span(
        client, workspace, session_factory, at, duration_ms=900, overhead_ms=900, attempts=0
    )

    body = await _overview(workspace, END - timedelta(hours=1))

    assert body["p95_upstream_ms"] == pytest.approx(280.0)
    assert (await _overview(workspace, END - timedelta(hours=7), END - timedelta(hours=6)))[
        "p95_upstream_ms"
    ] is None


async def test_overview_rate_limited_counts_kinds(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    at = END - timedelta(minutes=10)
    for kind in ("gateway", "tokens", None):
        await _gateway_span(client, workspace, session_factory, at, rate_limit_kind=kind)

    body = await _overview(workspace, END - timedelta(hours=1))

    assert (body["requests"], body["rate_limited"]) == (3, 2)


async def test_overview_series_hourly_gap_filled(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    start = END - timedelta(hours=3)
    hour = timedelta(hours=1)
    first = _on_the_clock(start, hour) + hour
    await _gateway_span(client, workspace, session_factory, first + timedelta(minutes=10))
    await _gateway_span(
        client, workspace, session_factory, first + timedelta(hours=1, minutes=5), status="error"
    )

    body = await _overview(workspace, start)

    assert body["series"]["bucket"] == "hour"
    points = _points(body)
    # Whole hours, so the axis reads "14:00"; the first bucket starts at or before `from`.
    assert all(moment.minute == 0 for moment, _, _ in points)
    assert points[0][0] <= start < points[0][0] + hour
    assert points == _expected(start, hour, {first: (1, 0), first + hour: (1, 1)})


async def test_overview_series_five_minute_for_one_hour(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    start = END - timedelta(hours=1)
    five = timedelta(minutes=5)
    second = _on_the_clock(start, five) + five
    await _gateway_span(client, workspace, session_factory, second + timedelta(minutes=2))

    body = await _overview(workspace, start)

    assert body["series"]["bucket"] == "5m"
    points = _points(body)
    assert all(moment.minute % 5 == 0 for moment, _, _ in points)
    assert points == _expected(start, five, {second: (1, 0)})


async def test_overview_series_respects_environment(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    start = END - timedelta(hours=2)
    await _gateway_span(client, workspace, session_factory, start + timedelta(minutes=5))
    await _gateway_span(
        client,
        workspace,
        session_factory,
        start + timedelta(minutes=70),
        status="error",
        environment="staging",
    )

    body = await _overview(workspace, start, environment="staging")

    assert body["requests"] == 1
    hour = timedelta(hours=1)
    staging = _on_the_clock(start + timedelta(minutes=70), hour)
    assert _points(body) == _expected(start, hour, {staging: (1, 1)})


async def test_overview_cross_tenant_reads_nothing(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: SessionFactory,
    browser_factory: Any,
) -> None:
    bob = await create_workspace(await browser_factory("bob@example.com"), org_name="Bob Co")
    start = END - timedelta(hours=1)
    await _gateway_span(
        client, bob, session_factory, start + timedelta(minutes=5), rate_limit_kind="gateway"
    )

    body = await _overview(workspace, start)

    assert (body["requests"], body["rate_limited"], body["previous"]["requests"]) == (0, 0, 0)
    assert all(requests == 0 for _, requests, _ in _points(body))
    response = await workspace.owner.get(
        f"/api/v1/projects/{bob.project_id}/gateway/overview",
        params={"from": start.isoformat(), "to": END.isoformat()},
    )
    assert response.status_code == 404

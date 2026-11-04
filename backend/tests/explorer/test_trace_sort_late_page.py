"""The trace list's page query for value sorts is late-materialized: an inner query picks the page
by `trace_id`, `started_at` and the sort value, and only those rows are read whole. Its pages,
order and cursors equal the query it replaced, for every sort and direction, with ties and
unknown values, filtered or not.
"""

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import event, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.traces import trace_summary
from app.db.models import Trace
from app.db.rls import bypass_rls
from app.explorer.trace_filters import apply_filters
from tests.explorer import trace_sort_before_t19 as before
from tests.helpers import Browser, Workspace, bearer, create_workspace, new_trace_id, span

NOW = datetime.now(UTC).replace(microsecond=0)
START = NOW - timedelta(hours=4)
WINDOW = {"from": START.isoformat(), "to": NOW.isoformat()}
SORTS = ["started_at", "duration", "cost", "tokens", "retry_cost_usd"]
GROUP = 3
PAGE = 2
# Per group of three traces that share a start time and every sort value, so each group is a full
# tie ordered by `trace_id` alone and, three rows over pages of two, always crosses a page break.
# Groups also repeat values at other start times, and each nullable sort has two NULL groups.
COSTS = [None, "0.10", "1.50", "0.10", None]
RETRY_COSTS = [None, "0.20", "0.00", "0.20", None]
TOKENS = [(None, None), (10, 5), (None, 15), (15, None), (None, None)]
DURATIONS = [100.0, 250.0, 100.0, 900.0, 250.0]
COUNT = GROUP * len(COSTS)
DEV = range(0, COUNT, 4)  # the traces the `environment=prod` filter drops
# `TraceSummaryOut` fields filled from per-page queries rather than the trace row.
PER_PAGE_FIELDS = {
    "models",
    "error_message",
    "error_class",
    "root_received",
    "scores",
    "input_preview",
    "output_preview",
}


@pytest.fixture
async def workspace(browser_factory: Any) -> Workspace:
    owner: Browser = await browser_factory("owner@example.com")
    return await create_workspace(owner)


@pytest.fixture
async def seeded(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> list[str]:
    ids = [new_trace_id() for _ in range(COUNT)]
    spans = [
        span(
            trace_id=trace_id,
            start=NOW - timedelta(minutes=60 - (index // GROUP) * 5),
            duration_ms=DURATIONS[index // GROUP],
            trace={"name": "answer", "environment": "dev" if index in DEV else "prod"},
        )
        for index, trace_id in enumerate(ids)
    ]
    response = await client.post(
        "/v1/traces", json={"spans": spans}, headers=bearer(workspace.api_key)
    )
    assert response.json()["rejected"] == [], response.text
    async with session_factory() as db:
        await bypass_rls(db)
        for index, trace_id in enumerate(ids):
            cost = COSTS[index // GROUP]
            retry_cost = RETRY_COSTS[index // GROUP]
            input_tokens, output_tokens = TOKENS[index // GROUP]
            await db.execute(
                update(Trace)
                .where(Trace.trace_id == trace_id)
                .values(
                    cost_usd=None if cost is None else Decimal(cost),
                    retry_cost_usd=None if retry_cost is None else Decimal(retry_cost),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )
            )
        await db.commit()
    return ids


@pytest.fixture
def statements() -> Iterator[list[tuple[str, Any]]]:
    seen: list[tuple[str, Any]] = []

    def record(_conn: Any, _cursor: Any, statement: str, parameters: Any, *_: Any) -> None:
        seen.append((statement, parameters))

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(Engine, "before_cursor_execute", record)


def _row(item: dict[str, Any]) -> dict[str, Any]:
    """A listed trace's fields that come from its row."""
    return {key: value for key, value in item.items() if key not in PER_PAGE_FIELDS}


def _as_listed(trace: Trace) -> dict[str, Any]:
    return trace_summary(trace, None, None, None, None, []).model_dump(mode="json")


async def _api_pages(
    workspace: Workspace, **params: Any
) -> list[tuple[list[dict[str, Any]], str | None]]:
    """Every page of the list as (rows, next cursor), following the cursors to the end."""
    url = f"/api/v1/projects/{workspace.project_id}/traces"
    pages: list[tuple[list[dict[str, Any]], str | None]] = []
    cursor: str | None = None
    for _ in range(COUNT + 1):
        query = {**WINDOW, **params, "limit": PAGE, **({"cursor": cursor} if cursor else {})}
        response = await workspace.owner.get(url, params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        cursor = body["next_cursor"]
        pages.append(([_row(item) for item in body["items"]], cursor))
        if cursor is None:
            return pages
    raise AssertionError("the pages did not end")


async def _oracle_pages(
    session_factory: async_sessionmaker[AsyncSession],
    workspace: Workspace,
    sort: Any,
    order: Any,
    environment: str | None,
) -> list[tuple[list[dict[str, Any]], str | None]]:
    """The same pages from the page query as it was before late materialization."""
    pages: list[tuple[list[dict[str, Any]], str | None]] = []
    cursor: str | None = None
    async with session_factory() as db:
        await bypass_rls(db)
        for _ in range(COUNT + 1):
            query = apply_filters(
                select(Trace).where(
                    Trace.project_id == workspace.project_id,
                    Trace.started_at >= START,
                    Trace.started_at < NOW,
                ),
                environment=environment,
                started_from=START,
            )
            query = before.apply(query, sort=sort, order=order, cursor=cursor).limit(PAGE + 1)
            rows = (await db.scalars(query)).all()
            page = rows[:PAGE]
            cursor = before.next_cursor(page[-1], sort) if len(rows) > PAGE else None
            pages.append(([_row(_as_listed(trace)) for trace in page], cursor))
            if cursor is None:
                return pages
    raise AssertionError("the pages did not end")


@pytest.mark.parametrize("environment", [None, "prod"])
@pytest.mark.parametrize("order", ["asc", "desc"])
@pytest.mark.parametrize("sort", SORTS)
async def test_pages_equal_the_query_before_late_materialization(
    workspace: Workspace,
    seeded: list[str],
    session_factory: async_sessionmaker[AsyncSession],
    sort: str,
    order: str,
    environment: str | None,
) -> None:
    filters = {"environment": environment} if environment else {}
    pages = await _api_pages(workspace, sort=sort, order=order, **filters)
    expected = await _oracle_pages(session_factory, workspace, sort, order, environment)

    assert pages == expected
    listed = [row["trace_id"] for rows, _ in pages for row in rows]
    assert len(listed) == len(set(listed)) == COUNT - (len(DEV) if environment else 0)


# Each value sort's value as the inner query selects it.
VALUE_SQL = {
    "duration": "traces.ended_at - traces.started_at",
    "cost": "traces.cost_usd",
    "tokens": (
        "coalesce(traces.input_tokens + traces.output_tokens, traces.input_tokens, "
        "traces.output_tokens)"
    ),
    "retry_cost_usd": "traces.retry_cost_usd",
}
# The page query: whole rows joined to a subquery that picks the page; the outer ORDER BY is last.
PAGE_QUERY = re.compile(
    r"SELECT traces\.\w+, .* FROM traces JOIN \(SELECT (?P<keys>[^()]*?(?:\([^()]*\))?[^()]*?) "
    r"FROM traces WHERE .* LIMIT %\((?P<limit>\w+)\)s::INTEGER\) AS (?P<alias>\w+) "
    r"ON .* ORDER BY (?P<order>[^()]*)"
)


@pytest.mark.parametrize("order", ["asc", "desc"])
@pytest.mark.parametrize("sort", SORTS[1:])
async def test_value_sort_page_query_selects_only_the_keys_before_the_rows(
    workspace: Workspace,
    seeded: list[str],
    statements: list[tuple[str, Any]],
    sort: str,
    order: str,
) -> None:
    url = f"/api/v1/projects/{workspace.project_id}/traces"
    params = {**WINDOW, "sort": sort, "order": order, "limit": PAGE}
    response = await workspace.owner.get(url, params=params)
    assert response.status_code == 200, response.text

    [(sql, parameters)] = [
        (sql, parameters)
        for sql, parameters in statements
        if "FROM traces" in sql and "LIMIT" in sql
    ]
    flat = " ".join(sql.split())
    page_query = PAGE_QUERY.fullmatch(flat)
    assert page_query is not None, flat
    keys = (
        f"traces.trace_id AS trace_id, traces.started_at AS started_at, {VALUE_SQL[sort]} AS value"
    )
    assert page_query["keys"] == keys
    assert parameters[page_query["limit"]] == PAGE + 1
    alias, direction = page_query["alias"], order.upper()
    assert page_query["order"] == (
        f"{alias}.value {direction} NULLS LAST, {alias}.started_at {direction}, "
        f"{alias}.trace_id {direction}"
    )

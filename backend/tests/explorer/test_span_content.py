"""Trace detail and session turns bound attributes, input and output together (`span_content`)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.explorer import trace_queries
from tests.helpers import Browser, Workspace, bearer, create_workspace, new_trace_id, span

START = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=4)
BIG = "x" * 10_000
TOO_LARGE = "SPAN_CONTENT_TOO_LARGE"


@pytest.fixture
async def workspace(browser_factory: Any) -> Workspace:
    owner: Browser = await browser_factory("owner@example.com")
    return await create_workspace(owner)


@pytest.fixture
def small_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    # About two 10 KB spans' worth of stored content.
    monkeypatch.setattr(trace_queries, "TRACE_PAYLOAD_BUDGET_BYTES", 25_000)


async def _ingest(client: httpx.AsyncClient, workspace: Workspace, spans: list[Any]) -> None:
    response = await client.post(
        "/v1/traces", json={"spans": spans}, headers=bearer(workspace.api_key)
    )
    assert response.json()["rejected"] == [], response.text


def _at(seconds: float) -> datetime:
    return START + timedelta(seconds=seconds)


def _span(trace_id: str, index: int, **overrides: Any) -> dict[str, Any]:
    return span(trace_id=trace_id, span_id=f"c{index:015d}", start=_at(index), **overrides)


def _flags(spans: list[dict[str, Any]]) -> list[tuple[bool, bool]]:
    return [(s["payload_omitted"], s["attributes_omitted"]) for s in spans]


async def test_trace_detail_bounds_attributes_and_payloads_together(
    client: httpx.AsyncClient, workspace: Workspace, browser_factory: Any, small_budget: None
) -> None:
    trace_id = new_trace_id()
    big_attributes = {"context": BIG}
    await _ingest(
        client,
        workspace,
        [
            # Attributes only, no payload: fits.
            _span(trace_id, 0, input=None, output=None, attributes=big_attributes),
            # Payload only: fits, and spends the rest of the budget.
            _span(trace_id, 1, input=BIG, output=None, attributes={}),
            # Over budget with both payloads absent: attributes withheld, payloads known null.
            _span(trace_id, 2, input=None, output=None, attributes=big_attributes),
            # Over budget with an empty-string output: content, so withheld too.
            _span(trace_id, 3, input=None, output="", attributes={"k": "v"}),
        ],
    )
    base = f"/api/v1/projects/{workspace.project_id}/traces/{trace_id}"

    refused = await workspace.owner.get(base)
    assert refused.status_code == 422
    assert refused.json()["code"] == TOO_LARGE
    assert "spans" not in refused.json()

    bounded = await workspace.owner.get(base, params={"span_content": "bounded"})
    spans = bounded.json()["spans"]
    assert [s["span_id"] for s in spans] == [f"c{i:015d}" for i in range(4)]
    assert _flags(spans) == [(False, False), (False, False), (False, True), (True, True)]
    assert spans[0]["attributes"] == big_attributes
    assert spans[1]["input"] == BIG
    assert [s["attributes"] for s in spans[2:]] == [{}, {}]
    assert [(s["input"], s["output"]) for s in spans[2:]] == [(None, None), (None, None)]
    # The withheld content is not in the response: only span 0's attributes and span 1's input.
    assert bounded.text.count(BIG) == 2

    for omitted in spans[2:]:
        full = (await workspace.owner.get(f"{base}/spans/{omitted['span_id']}")).json()
        assert full["payload_omitted"] is False
        assert full["attributes_omitted"] is False
    assert (await workspace.owner.get(f"{base}/spans/{spans[2]['span_id']}")).json()[
        "attributes"
    ] == big_attributes
    assert (await workspace.owner.get(f"{base}/spans/{spans[3]['span_id']}")).json()["output"] == ""

    unsupported = await workspace.owner.get(base, params={"span_content": "full"})
    assert unsupported.status_code == 422
    assert unsupported.json()["code"] == "VALIDATION_ERROR"

    other: Browser = await browser_factory("other@example.com")
    other_ws = await create_workspace(other)
    assert (
        await other.get(
            f"/api/v1/projects/{other_ws.project_id}/traces/{trace_id}",
            params={"span_content": "bounded"},
        )
    ).status_code == 404
    assert (await other.get(base, params={"span_content": "bounded"})).status_code == 404


async def test_small_trace_is_complete_without_the_selector(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    trace_id = new_trace_id()
    await _ingest(client, workspace, [_span(trace_id, 0)])
    base = f"/api/v1/projects/{workspace.project_id}/traces/{trace_id}"

    for params in ({}, {"span_content": "bounded"}):
        detail = (await workspace.owner.get(base, params=params)).json()
        (only,) = detail["spans"]
        assert (only["payload_omitted"], only["attributes_omitted"]) == (False, False)
        assert only["attributes"] == {"temperature": 0.2}
        assert only["retry_eligible"] is True


async def test_retry_eligible_follows_the_stored_retry_predicate(
    client: httpx.AsyncClient, workspace: Workspace, small_budget: None
) -> None:
    trace_id = new_trace_id()
    await _ingest(
        client,
        workspace,
        [
            _span(trace_id, 0),
            _span(trace_id, 1, attributes={"spanlight.cache": "hit"}),
            _span(trace_id, 2, attributes={"spanlight.cache": {"state": "hit"}}),
            _span(trace_id, 3, attributes={"spanlight.fault.scenario": None}),
            _span(trace_id, 4, attributes={"spanlight.fault.scenario": 0}),
            _span(trace_id, 5, kind="tool", model=None, provider=None, usage=None),
            _span(trace_id, 6, input=None),
            # Over budget from here on: eligibility still comes from the stored attributes.
            _span(trace_id, 7, attributes={"spanlight.cache": "hit", "pad": BIG * 3}),
            _span(trace_id, 8, attributes={"pad": BIG}),
            _span(trace_id, 9, attributes={"spanlight.fault.scenario": "timeout", "pad": BIG}),
        ],
    )
    base = f"/api/v1/projects/{workspace.project_id}/traces/{trace_id}"

    spans = (await workspace.owner.get(base, params={"span_content": "bounded"})).json()["spans"]

    assert [s["attributes_omitted"] for s in spans[7:]] == [True, True, True]
    assert [s["retry_eligible"] for s in spans] == [
        True,
        False,
        True,
        False,
        False,
        False,
        False,
        False,
        True,
        False,
    ]
    full = (await workspace.owner.get(f"{base}/spans/{spans[7]['span_id']}")).json()
    assert full["retry_eligible"] is False
    full = (await workspace.owner.get(f"{base}/spans/{spans[8]['span_id']}")).json()
    assert full["retry_eligible"] is True


async def test_legacy_rows_without_a_byte_count_follow_their_stored_payloads(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    small_budget: None,
) -> None:
    trace_id = new_trace_id()
    await _ingest(
        client,
        workspace,
        [_span(trace_id, 0, input=None, output=None), _span(trace_id, 1, input="asked")],
    )
    async with session_factory() as db, db.begin():
        await db.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
        await db.execute(
            text("UPDATE spans SET payload_bytes = NULL WHERE trace_id = :trace_id"),
            {"trace_id": trace_id},
        )
        # A JSON null (as opposed to SQL NULL) is the other way a payload is known absent.
        await db.execute(
            text("UPDATE spans SET input = 'null'::jsonb WHERE span_id = :span_id"),
            {"span_id": f"c{0:015d}"},
        )
    base = f"/api/v1/projects/{workspace.project_id}/traces/{trace_id}"

    # The legacy fallback (64 KiB a span) overflows the budget from the first span on.
    spans = (await workspace.owner.get(base, params={"span_content": "bounded"})).json()["spans"]

    assert _flags(spans) == [(False, True), (True, True)]
    assert spans[0]["input"] is None
    assert (await workspace.owner.get(f"{base}/spans/{spans[1]['span_id']}")).json()[
        "input"
    ] == "asked"


async def test_exact_boundary_keeps_the_span_inline(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace_id = new_trace_id()
    await _ingest(client, workspace, [_span(trace_id, 0), _span(trace_id, 1, input="asked")])
    async with session_factory() as db:
        await db.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
        weights = await db.execute(
            text(
                "SELECT payload_bytes + octet_length(attributes::text) FROM spans"
                " WHERE trace_id = :trace_id ORDER BY started_at, span_id"
            ),
            {"trace_id": trace_id},
        )
        total = sum(weight for (weight,) in weights)
    base = f"/api/v1/projects/{workspace.project_id}/traces/{trace_id}"

    async def flags(budget: int) -> list[tuple[bool, bool]]:
        monkeypatch.setattr(trace_queries, "TRACE_PAYLOAD_BUDGET_BYTES", budget)
        response = await workspace.owner.get(base, params={"span_content": "bounded"})
        spans = response.json()["spans"]
        assert spans[1]["input"] == ("asked" if budget >= total else None)
        return _flags(spans)

    # A running sum equal to the budget is inline; one byte less withholds the last span.
    assert await flags(total) == [(False, False), (False, False)]
    assert await flags(total - 1) == [(False, False), (True, True)]


async def test_session_turns_bound_a_page_of_content_together(
    client: httpx.AsyncClient, workspace: Workspace, small_budget: None
) -> None:
    ids = [new_trace_id() for _ in range(3)]
    await _ingest(
        client,
        workspace,
        [
            span(
                trace_id=trace_id,
                start=_at(index * 60),
                input=None,
                output=None,
                attributes={"context": BIG},
                trace={"session_id": "big"},
            )
            for index, trace_id in enumerate(ids)
        ],
    )
    url = f"/api/v1/projects/{workspace.project_id}/sessions/big/turns"

    refused = await workspace.owner.get(url)
    assert refused.status_code == 422
    assert refused.json()["code"] == TOO_LARGE

    items = (await workspace.owner.get(url, params={"span_content": "bounded"})).json()["items"]
    spans = [item["spans"][0] for item in items]
    # Newest first; the third turn's attributes are withheld, its payloads stay known null.
    assert _flags(spans) == [(False, False), (False, False), (False, True)]
    assert spans[2]["attributes"] == {}
    assert spans[2]["input"] is None

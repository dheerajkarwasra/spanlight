"""`GET /projects/{project_id}/traces/{trace_id}/spans/{span_id}/cost-explanation` (spec §8.1).

Every span here is stored by the real pipeline (`POST /v1/traces`, or the gateway for the cache
hit), so each case explains a row exactly as ingestion priced it. The pipeline's clock is pinned
after the 2026-10-16 snapshot takes effect, so spans can start in it whatever the wall clock says.

Failure modes: a response that drifts from the §8.1 shape (money as a float, an exponent such as
`0E-8`, a missing key); a stored figure re-priced with today's rows instead of explained; a
deleted override or a vanished snapshot reported as a mismatch; a mismatch that is not counted
or that logs the span's payload; another tenant's span becoming readable.
"""

import logging
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.observability import COST_EXPLANATION_MISMATCH, FEATURE_USED
from app.db.rls import bypass_rls
from app.ingest import pipeline
from app.pricing.prices import EFFECTIVE_FROM
from tests.budget_fixtures import app_on_clock
from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import (
    MESSAGES_BODY,
    PILOT_MESSAGE,
    FakeProvider,
    ManualClock,
    logged,
    metric_delta,
    read_gateway_spans,
)
from tests.helpers import (
    Workspace,
    bearer,
    create_api_key,
    create_project,
    create_workspace,
    new_span_id,
    new_trace_id,
    span,
)
from tests.ingest.support import ingest_native, workspace_for_project

SPAN_START = EFFECTIVE_FROM + timedelta(hours=12)  # 2026-10-16T12:00Z
PINNED_NOW = SPAN_START + timedelta(hours=1)

# Spec §8.1: Claude Sonnet 4.6 at $3 / $15 per million, cache reads at $0.30.
SPEC_USAGE: dict[str, Any] = {
    "input_tokens": 60510,
    "output_tokens": 812,
    "cached_tokens": 48200,
    "cache_write_tokens": 0,
    "cache_write_1h_tokens": 0,
    "service_tier": "standard",
}


@pytest.fixture(autouse=True)
def clock_after_snapshot(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Pin the ingestion clock after the 2026-10-16 prices and fees take effect."""

    class _Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return PINNED_NOW.astimezone(tz) if tz is not None else PINNED_NOW.replace(tzinfo=None)

    monkeypatch.setattr(pipeline, "datetime", _Clock)
    return PINNED_NOW


@pytest.fixture
def pricing_clock() -> ManualClock:
    """The gateway's clock: an hour after the 2026-10-16 prices take effect."""
    return ManualClock(start=EFFECTIVE_FROM + timedelta(hours=1))


@pytest.fixture
async def gateway_app(
    gateway_settings: Settings, fake_provider: FakeProvider, pricing_clock: ManualClock
) -> AsyncIterator[Any]:
    async with app_on_clock(gateway_settings, fake_provider, pricing_clock) as application:
        yield application


@pytest.fixture
async def workspace(browser_factory: Any) -> Workspace:
    return await create_workspace(await browser_factory("owner@example.com"))


def _sonnet_span(
    trace_id: str, usage: dict[str, Any] | None = None, **fields: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "messages claude-sonnet-4-6",
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "usage": dict(SPEC_USAGE) if usage is None else usage,
        "attributes": {"usage.reasoning_tokens": 300},
    }
    body.update(fields)
    return span(trace_id=trace_id, start=SPAN_START, **body)


async def _ingest(client: httpx.AsyncClient, workspace: Workspace, body: dict[str, Any]) -> None:
    response = await ingest_native(client, workspace, [body])
    assert response.status_code == 200, response.text
    assert response.json()["accepted"] == 1, response.text


def _url(project_id: object, trace_id: str, span_id: str) -> str:
    return f"/api/v1/projects/{project_id}/traces/{trace_id}/spans/{span_id}/cost-explanation"


async def _explain(workspace: Workspace, body: dict[str, Any]) -> dict[str, Any]:
    response = await workspace.owner.get(
        _url(workspace.project_id, body["trace_id"], body["span_id"])
    )
    assert response.status_code == 200, response.text
    explained: dict[str, Any] = response.json()
    return explained


async def _update_span(
    session_factory: async_sessionmaker[AsyncSession], span_id: str, assignment: str, value: Any
) -> None:
    async with session_factory() as db:
        await bypass_rls(db)
        await db.execute(
            text(f"UPDATE spans SET {assignment} = :value WHERE span_id = :span_id"),
            {"value": value, "span_id": span_id},
        )
        await db.commit()


async def test_ok_shape_matches_spec_example(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    body = _sonnet_span(new_trace_id())
    await _ingest(client, workspace, body)

    explained = await _explain(workspace, body)

    assert explained == {
        "span_id": body["span_id"],
        "trace_id": body["trace_id"],
        "status": "ok",
        "provider": "anthropic",
        "provider_canonical": "anthropic",
        "model": "claude-sonnet-4-6",
        "service_tier": "standard",
        "tier_multiplier": "1",
        "tier_note": None,
        "price": {
            "source": "seed",
            "version": "2026-10-16",
            "model_pattern": "claude-sonnet-4-6",
            "effective_from": "2026-10-16T00:00:00Z",
            "min_prompt_tokens": 0,
            "override_id": None,
        },
        "usage": {
            "input_tokens": 60510,
            "output_tokens": 812,
            "cached_tokens": 48200,
            "cache_write_tokens": 0,
            "cache_write_1h_tokens": 0,
            "web_search_requests": None,
            "reasoning_tokens": 300,
            "unreported_reason": None,
        },
        "lines": [
            {
                "kind": "uncached_input",
                "quantity": 12310,
                "unit": "tokens",
                "rate": "3.000000",
                "rate_unit": "usd_per_mtok",
                "multiplier": "1",
                "usd": "0.03693000",
            },
            {
                "kind": "cache_read",
                "quantity": 48200,
                "unit": "tokens",
                "rate": "0.300000",
                "rate_unit": "usd_per_mtok",
                "multiplier": "1",
                "usd": "0.01446000",
            },
            {
                "kind": "cache_write_5m",
                "quantity": 0,
                "unit": "tokens",
                "rate": "3.750000",
                "rate_unit": "usd_per_mtok",
                "multiplier": "1",
                "usd": "0",
            },
            {
                "kind": "cache_write_1h",
                "quantity": 0,
                "unit": "tokens",
                "rate": "6.000000",
                "rate_unit": "usd_per_mtok",
                "multiplier": "1",
                "usd": "0",
            },
            {
                "kind": "output",
                "quantity": 812,
                "unit": "tokens",
                "rate": "15.000000",
                "rate_unit": "usd_per_mtok",
                "multiplier": "1",
                "usd": "0.01218000",
            },
        ],
        "output_breakdown": {"reasoning": 300, "other": 512},
        "recomputed_usd": "0.06357000",
        "stored_usd": "0.06357000",
        "stored_fee_usd": None,
        "message": None,
        # Spec §7.1: shown on a gateway cache hit only, so null here.
        "original_usage": None,
    }


async def test_web_search_line_and_fee_are_explained(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    usage = {**SPEC_USAGE, "server_tool_use": {"web_search_requests": 3}}
    body = _sonnet_span(new_trace_id(), usage=usage)
    await _ingest(client, workspace, body)

    explained = await _explain(workspace, body)

    assert explained["status"] == "ok"
    assert explained["lines"][-1] == {
        "kind": "web_search",
        "quantity": 3,
        "unit": "requests",
        "rate": "10.000000",
        "rate_unit": "usd_per_1k_requests",
        "multiplier": "1",
        "usd": "0.03000000",
    }
    assert explained["usage"]["web_search_requests"] == 3
    assert explained["stored_fee_usd"] == "0.03000000"
    assert explained["recomputed_usd"] == explained["stored_usd"] == "0.09357000"


async def test_no_usage_with_reason(client: httpx.AsyncClient, workspace: Workspace) -> None:
    body = _sonnet_span(
        new_trace_id(),
        usage={"input_tokens": 1200},
        attributes={"usage.unreported_reason": "client_disconnected"},
    )
    await _ingest(client, workspace, body)

    explained = await _explain(workspace, body)

    assert explained["status"] == "no_usage"
    assert explained["lines"] == []
    assert explained["price"] is None
    assert explained["recomputed_usd"] is None
    assert explained["stored_usd"] is None
    assert explained["usage"]["output_tokens"] is None
    assert explained["usage"]["unreported_reason"] == "client_disconnected"
    assert explained["message"] == (
        "Output tokens were not reported: the client disconnected, so the cost is unknown."
    )


async def test_unknown_unreported_reason_reads_as_null(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    body = _sonnet_span(
        new_trace_id(),
        usage={"input_tokens": 1200},
        attributes={"usage.unreported_reason": "gremlins"},
    )
    await _ingest(client, workspace, body)

    explained = await _explain(workspace, body)

    assert explained["status"] == "no_usage"
    # Only the six documented reasons are echoed; anything else is unknown, not passed through.
    assert explained["usage"]["unreported_reason"] is None


async def test_no_price(client: httpx.AsyncClient, workspace: Workspace) -> None:
    body = _sonnet_span(new_trace_id(), model="claude-unknown-model")
    await _ingest(client, workspace, body)

    explained = await _explain(workspace, body)

    assert explained["status"] == "no_price"
    assert (explained["price"], explained["lines"]) == (None, [])
    assert (explained["recomputed_usd"], explained["stored_usd"]) == (None, None)
    assert explained["model"] == "claude-unknown-model"
    assert explained["message"] == (
        "No price matched anthropic claude-unknown-model when the span was ingested, "
        "so the cost is unknown."
    )


async def test_cache_hit(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # Seeded as in tests/gateway/test_cache_write_usage.py: one upstream answer, then a hit.
    keys = f"/api/v1/projects/{gateway_workspace.project_id}/gateway/keys"
    edited = await gateway_workspace.owner.patch(
        f"{keys}/{gateway_workspace.gateway_key_id}", json={"cache_ttl_seconds": 600}
    )
    assert edited.status_code == 200, edited.text
    fake_provider.reply_json(PILOT_MESSAGE)
    for _ in range(2):
        response = await gateway_workspace.gw.post("/gw/v1/messages", json=MESSAGES_BODY)
        assert response.status_code == 200, response.text
    spans = await read_gateway_spans(session_factory, gateway_workspace.project_id)
    [hit] = [row for row in spans if row["attributes"]["spanlight.cache"] == "hit"]

    response = await gateway_workspace.owner.get(
        _url(gateway_workspace.project_id, hit["trace_id"], hit["span_id"])
    )

    assert response.status_code == 200, response.text
    explained = response.json()
    assert explained["status"] == "cache_hit"
    assert [line["quantity"] for line in explained["lines"]] == [0, 0, 0, 0, 0]
    assert {line["usd"] for line in explained["lines"]} == {"0"}
    assert explained["recomputed_usd"] == "0.00000000"
    assert explained["stored_usd"] == "0.00000000"
    assert explained["original_usage"] == {
        "input_tokens": 6423,
        "output_tokens": 12,
        "cached_tokens": 0,
        "cache_write_tokens": 6404,
        "cache_write_1h_tokens": 6404,
    }
    assert explained["message"] == (
        "Gateway cache hit: the response was served from the cache, so the cost is 0."
    )


async def test_usage_rollup(client: httpx.AsyncClient, workspace: Workspace) -> None:
    """A span whose usage repeats a child's is a rollup: explained, priced, never a mismatch."""
    trace_id = new_trace_id()
    parent = _sonnet_span(trace_id)
    child = _sonnet_span(trace_id, parent_span_id=parent["span_id"])
    await _ingest(client, workspace, parent)
    await _ingest(client, workspace, child)
    mismatches = metric_delta(COST_EXPLANATION_MISMATCH)

    explained = await _explain(workspace, parent)

    assert explained["status"] == "usage_rollup"
    assert explained["recomputed_usd"] == explained["stored_usd"] == "0.06357000"
    assert explained["message"] == (
        "Usage rollup: this span repeats the usage of its child spans and is excluded from totals."
    )
    assert mismatches.value == 0
    assert (await _explain(workspace, child))["status"] == "ok"


async def test_override_deleted(client: httpx.AsyncClient, workspace: Workspace) -> None:
    overrides = f"/api/v1/orgs/{workspace.org_id}/price-overrides"
    created = await workspace.owner.post(
        overrides,
        json={
            "provider": "anthropic",
            "model_pattern": "claude-sonnet-4-6",
            "input_per_mtok": "1.00",
            "output_per_mtok": "2.00",
            "effective_from": EFFECTIVE_FROM.isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    override_id = created.json()["id"]
    body = _sonnet_span(new_trace_id())
    await _ingest(client, workspace, body)
    priced = await _explain(workspace, body)
    assert priced["status"] == "ok"
    assert priced["price"]["source"] == "override"
    assert priced["price"]["version"] == f"override:{override_id}"
    assert priced["price"]["override_id"] == override_id
    stored = priced["stored_usd"]

    deleted = await workspace.owner.delete(f"{overrides}/{override_id}")
    assert deleted.status_code == 204, deleted.text
    explained = await _explain(workspace, body)

    assert explained["status"] == "override_deleted"
    assert (explained["price"], explained["lines"]) == (None, [])
    assert explained["recomputed_usd"] is None
    assert explained["stored_usd"] == stored
    assert explained["message"] == (
        f"The price override that priced this span (override:{override_id}) was deleted; "
        "the stored figure is shown as it was priced."
    )


async def test_price_version_missing(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = _sonnet_span(new_trace_id())
    await _ingest(client, workspace, body)
    await _update_span(session_factory, body["span_id"], "pricing_version", "1999-01-01")

    explained = await _explain(workspace, body)

    assert explained["status"] == "price_version_missing"
    assert (explained["price"], explained["lines"]) == (None, [])
    assert explained["stored_usd"] == "0.06357000"
    assert explained["message"] == (
        "The price row that priced this span (version 1999-01-01) is no longer in the price "
        "table; the stored figure is shown as it was priced."
    )


async def test_price_version_without_a_row_for_the_model(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = _sonnet_span(new_trace_id())
    await _ingest(client, workspace, body)
    # The span's version still exists, but holds no row for the model it now names.
    await _update_span(session_factory, body["span_id"], "model", "claude-unknown-model")

    explained = await _explain(workspace, body)

    assert explained["status"] == "price_version_missing"
    assert (explained["price"], explained["lines"]) == (None, [])
    assert explained["recomputed_usd"] is None
    assert explained["stored_usd"] == "0.06357000"
    assert explained["message"] == (
        "The price row that priced this span (version 2026-10-16) is no longer in the price "
        "table; the stored figure is shown as it was priced."
    )


async def test_mismatch_counts_and_logs(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    body = _sonnet_span(new_trace_id())
    await _ingest(client, workspace, body)
    await _update_span(session_factory, body["span_id"], "cost_usd", Decimal("0.07000000"))
    mismatches = metric_delta(COST_EXPLANATION_MISMATCH)

    with caplog.at_level(logging.WARNING):
        explained = await _explain(workspace, body)

    assert explained["status"] == "mismatch"
    assert explained["recomputed_usd"] == "0.06357000"
    assert explained["stored_usd"] == "0.07000000"
    assert explained["message"] == (
        "Recomputed 0.06357000 differs from stored 0.07000000. "
        "This is a bug in Spanlight; report it with the span id."
    )
    assert mismatches.value == 1
    [event] = logged(caplog, "cost_explanation_mismatch")
    assert {key: event.get(key) for key in ("project_id", "span_id", "stored", "recomputed")} == {
        "project_id": workspace.project_id,
        "span_id": body["span_id"],
        "stored": "0.07000000",
        "recomputed": "0.06357000",
    }
    # Ids and both figures only: nothing of the span's payload, model or usage.
    assert not set(event) & {"input", "output", "attributes", "usage", "model", "lines", "trace"}


async def test_non_member_404(
    client: httpx.AsyncClient, workspace: Workspace, browser_factory: Any
) -> None:
    body = _sonnet_span(new_trace_id())
    await _ingest(client, workspace, body)
    outsider = await browser_factory("eve@example.com")
    other = await workspace_for_project(
        workspace.owner,
        workspace.org_id,
        await create_project(workspace.owner, workspace.org_id, "Other"),
    )
    path = _url(workspace.project_id, body["trace_id"], body["span_id"])

    assert (await outsider.get(path)).status_code == 404
    # The span is not visible through another project of the same organization.
    other_path = _url(other.project_id, body["trace_id"], body["span_id"])
    assert (await workspace.owner.get(other_path)).status_code == 404
    # Nor under another trace id, nor with an unknown span id.
    wrong_trace = _url(workspace.project_id, new_trace_id(), body["span_id"])
    assert (await workspace.owner.get(wrong_trace)).status_code == 404
    unknown_span = _url(workspace.project_id, body["trace_id"], new_span_id())
    response = await workspace.owner.get(unknown_span)
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def test_api_key_traces_read_allowed(
    bare_client: httpx.AsyncClient, client: httpx.AsyncClient, workspace: Workspace
) -> None:
    body = _sonnet_span(new_trace_id())
    await _ingest(client, workspace, body)
    read = await create_api_key(workspace.owner, workspace.project_id, scopes=["traces:read"])
    path = _url(workspace.project_id, body["trace_id"], body["span_id"])

    allowed = await bare_client.get(path, headers=bearer(read["secret"]))
    ingest_only = await bare_client.get(path, headers=bearer(workspace.api_key))

    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["status"] == "ok"
    assert ingest_only.status_code == 403
    assert ingest_only.json()["code"] == "KEY_SCOPE"


async def test_feature_counter_increments(client: httpx.AsyncClient, workspace: Workspace) -> None:
    body = _sonnet_span(new_trace_id())
    await _ingest(client, workspace, body)
    uses = metric_delta(FEATURE_USED, feature="cost_explanation")

    await _explain(workspace, body)
    missing = await workspace.owner.get(_url(workspace.project_id, body["trace_id"], new_span_id()))

    assert missing.status_code == 404
    assert uses.value == 1  # a span that is not visible is not a use

"""OTLP service tier and web search attributes: mapped, normalized, stored and priced.

The tier comes from `openai.response.service_tier`, then `gen_ai.openai.response.service_tier`,
then `spanlight.usage.service_tier`, folded onto the four stored values by `normalize_tier`; an
unknown spelling leaves the tier unset and is kept in `attributes["usage.service_tier_raw"]`
(never a rejected span). Web search counts come from `spanlight.usage.web_search_requests` and
are fitted like the token counts. Prices are the 2026-10-16 snapshot, so the pipeline's clock is
pinned after it takes effect and every span starts inside it.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Span
from app.ingest import pipeline
from app.ingest.otlp import OtlpSpan, to_native_span
from app.pricing.prices import EFFECTIVE_FROM
from tests.gateway_support import logged
from tests.helpers import Workspace, bearer
from tests.ingest.support import otlp_json_document, otlp_json_span, stored_spans

SPAN_START = EFFECTIVE_FROM + timedelta(hours=12)  # 2026-10-16T12:00Z
PINNED_NOW = SPAN_START + timedelta(hours=1)
THREE_SEARCHES_FEE = Decimal("0.03000000")  # $10 per 1,000 web searches


@pytest.fixture(autouse=True)
def clock_after_snapshot(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Pin the ingestion clock after the 2026-10-16 prices and fees take effect."""

    class _Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return PINNED_NOW.astimezone(tz) if tz is not None else PINNED_NOW.replace(tzinfo=None)

    monkeypatch.setattr(pipeline, "datetime", _Clock)
    return PINNED_NOW


def _llm_span(provider_key: str, provider: str, model: str, **values: Any) -> dict[str, Any]:
    """An OTLP/JSON LLM span at `SPAN_START` with usage; `values` are extra typed attributes."""
    span = otlp_json_span({provider_key: provider, "gen_ai.response.model": model})
    start_ns = int(SPAN_START.timestamp() * 1e9)
    span["startTimeUnixNano"] = str(start_ns)
    span["endTimeUnixNano"] = str(start_ns + 500_000_000)
    span["attributes"] += [
        {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "20000"}},
        {"key": "gen_ai.usage.output_tokens", "value": {"intValue": "1000"}},
    ]
    span["attributes"] += [{"key": key, "value": value} for key, value in values.items()]
    return span


def _openai_span(**values: Any) -> dict[str, Any]:
    return _llm_span("gen_ai.provider.name", "openai", "gpt-4o", **values)


def _sonnet_span(**values: Any) -> dict[str, Any]:
    return _llm_span("gen_ai.provider.name", "anthropic", "claude-sonnet-4-6", **values)


def _text(value: str) -> dict[str, str]:
    return {"stringValue": value}


async def _ingest(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    *spans: dict[str, Any],
) -> list[Span]:
    response = await client.post(
        "/v1/otlp/traces", json=otlp_json_document(*spans), headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200 and response.json() == {}
    return await stored_spans(session_factory)


def _native_usage(attributes: dict[str, Any]) -> dict[str, Any]:
    """The native `usage` of an OTLP span carrying `attributes` (no database)."""
    span = OtlpSpan(
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        span_id="00f067aa0ba902b7",
        parent_span_id=None,
        name="chat gpt-4o",
        kind=3,
        start_unix_nano=1_760_000_000_000_000_000,
        end_unix_nano=1_760_000_000_500_000_000,
        status_code=1,
        status_message=None,
        attributes={"gen_ai.provider.name": "openai", **attributes},
        resource={},
    )
    usage: dict[str, Any] = to_native_span(span)["usage"]
    return usage


async def test_openai_response_service_tier_attribute_is_read(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    span = _openai_span(**{"openai.response.service_tier": _text("flex")})

    [stored] = await _ingest(client, workspace, session_factory, span)

    assert stored.service_tier == "flex"
    assert "usage.service_tier_raw" not in stored.attributes


def test_gen_ai_openai_name_wins_over_spanlight_name() -> None:
    usage = _native_usage(
        {
            "spanlight.usage.service_tier": "batch",
            "gen_ai.openai.response.service_tier": "flex",
        }
    )

    assert usage["service_tier"] == "flex"


def test_openai_response_name_wins_over_both_others() -> None:
    usage = _native_usage(
        {
            "spanlight.usage.service_tier": "batch",
            "gen_ai.openai.response.service_tier": "flex",
            "openai.response.service_tier": "priority",
        }
    )

    assert usage["service_tier"] == "priority"


def test_spanlight_name_is_read_when_alone() -> None:
    assert _native_usage({"spanlight.usage.service_tier": "batch"})["service_tier"] == "batch"


@pytest.mark.parametrize(
    ("sent", "kept"),
    [
        pytest.param("  Turbo ", "Turbo", id="padded-kept-trimmed"),
        pytest.param("   ", None, id="blank-not-kept"),
        pytest.param("t" * 200, "t" * 64, id="cut-to-64"),
    ],
)
def test_unknown_spelling_is_kept_trimmed(sent: str, kept: str | None) -> None:
    span = OtlpSpan(
        trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
        span_id="00f067aa0ba902b7",
        parent_span_id=None,
        name="chat gpt-4o",
        kind=3,
        start_unix_nano=1_760_000_000_000_000_000,
        end_unix_nano=1_760_000_000_500_000_000,
        status_code=1,
        status_message=None,
        attributes={"gen_ai.provider.name": "openai", "openai.response.service_tier": sent},
        resource={},
    )

    native = to_native_span(span)

    assert "service_tier" not in native["usage"]
    assert native["attributes"].get("usage.service_tier_raw") == kept


def test_absent_tier_and_searches_add_no_usage_fields() -> None:
    usage = _native_usage({"gen_ai.usage.input_tokens": 10})

    assert "service_tier" not in usage
    assert "server_tool_use" not in usage


async def test_unknown_spelling_kept_raw(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    scale = _openai_span(**{"openai.response.service_tier": _text("scale")})
    turbo = _openai_span(**{"openai.response.service_tier": _text("turbo")})

    stored = await _ingest(client, workspace, session_factory, scale, turbo)

    by_id = {row.span_id: row for row in stored}
    known, unknown = by_id[scale["spanId"]], by_id[turbo["spanId"]]
    assert known.service_tier == "standard"
    assert "usage.service_tier_raw" not in known.attributes
    assert unknown.service_tier is None
    assert unknown.attributes["usage.service_tier_raw"] == "turbo"
    assert unknown.cost_usd is not None  # an unknown tier is priced at the standard rate


async def test_web_search_requests_attribute_priced(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    span = _sonnet_span(**{"spanlight.usage.web_search_requests": {"intValue": "3"}})

    [stored] = await _ingest(client, workspace, session_factory, span)

    assert stored.web_search_requests == 3
    assert stored.fee_usd == THREE_SEARCHES_FEE
    # 20,000 input at $3 and 1,000 output at $15 per million, plus the fee.
    assert stored.cost_usd == Decimal("0.07500000") + THREE_SEARCHES_FEE


@pytest.mark.parametrize(
    "not_whole",
    [
        pytest.param({"doubleValue": "NaN"}, id="nan"),
        pytest.param({"doubleValue": 2.5}, id="fraction"),
        pytest.param({"doubleValue": "Infinity"}, id="infinity"),
    ],
)
async def test_web_search_double_coerced_and_nan_dropped(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
    not_whole: dict[str, Any],
) -> None:
    whole = _sonnet_span(**{"spanlight.usage.web_search_requests": {"doubleValue": 3.0}})
    nan = _sonnet_span(**{"spanlight.usage.web_search_requests": not_whole})

    stored = await _ingest(client, workspace, session_factory, whole, nan)

    by_id = {row.span_id: row for row in stored}
    coerced, dropped = by_id[whole["spanId"]], by_id[nan["spanId"]]
    assert (coerced.web_search_requests, coerced.fee_usd) == (3, THREE_SEARCHES_FEE)
    assert (dropped.web_search_requests, dropped.fee_usd) == (None, None)
    assert dropped.cost_usd == Decimal("0.07500000")  # the tokens are still priced
    (warning,) = logged(caplog, "otlp_values_dropped")
    assert warning["token_counts"] == 1


async def test_gemini_provider_alias_prices(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    span = _llm_span("gen_ai.system", "gcp.gemini", "gemini-2.5-flash")

    [stored] = await _ingest(client, workspace, session_factory, span)

    assert stored.provider == "gcp.gemini"
    # 20,000 input at $0.30 and 1,000 output at $2.50 per million.
    assert stored.cost_usd == Decimal("0.00850000")

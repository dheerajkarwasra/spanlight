"""The `provider.*` attributes at ingest: filtered whoever sent them, read into the three span
columns, and exposed on the trace detail.
"""

import secrets
import time
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Span
from tests.helpers import Workspace, bearer, new_trace_id, span
from tests.ingest.support import ingest_native, stored_spans, telemetry_rows

SPEND_CAP_ATTRIBUTES: dict[str, Any] = {
    "http.status_code": 429,
    "provider.error.type": "rate_limit_error",
    "provider.error.code": "enforced_spend_limit_reached",
    "provider.request_id": "req_011CSpendCap",
    "provider.retry_after_s": 30,
    "provider.ratelimit": {
        "retry-after": "30",
        "anthropic-ratelimit-tokens-remaining": "0",
    },
}
JUNK_HEADER_COUNT = 40
AUTH_HEADERS = ("authorization", "x-api-key", "cookie", "set-cookie")


def _failed_span(trace_id: str, attributes: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "name": "messages claude-haiku-4-5",
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "status": "error",
        "status_message": "This organization has reached its spend limit.",
        "attributes": attributes,
    }
    fields.update(overrides)
    return span(trace_id=trace_id, **fields)


async def _only_span(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    body: dict[str, Any],
) -> Span:
    response = await ingest_native(client, workspace, [body])
    assert response.status_code == 200, response.text
    assert response.json()["rejected"] == [], response.text
    (stored,) = await stored_spans(session_factory)
    return stored


async def test_native_span_with_provider_attributes_fills_columns(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    body = _failed_span(new_trace_id(), dict(SPEND_CAP_ATTRIBUTES))

    stored = await _only_span(client, workspace, session_factory, body)

    assert stored.provider_error_type == "rate_limit_error"
    assert stored.provider_error_code == "enforced_spend_limit_reached"
    assert stored.rate_limit_kind == "spend_cap"
    assert stored.error_class == "rate_limit"
    assert stored.attributes["provider.ratelimit"] == SPEND_CAP_ATTRIBUTES["provider.ratelimit"]
    assert stored.attributes["provider.request_id"] == "req_011CSpendCap"
    assert stored.attributes["provider.retry_after_s"] == 30.0


async def test_junk_ratelimit_headers_are_filtered_at_ingest(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    headers = {f"x-junk-header-{index}": "1" for index in range(JUNK_HEADER_COUNT)}
    headers["anthropic-ratelimit-requests-limit"] = "50"
    headers["anthropic-ratelimit-requests-remaining"] = "0"
    headers["anthropic-ratelimit-requests-reset"] = "x" * 70  # an allowlisted name, too long
    attributes = {"http.status_code": 429, "provider.ratelimit": headers}

    stored = await _only_span(
        client, workspace, session_factory, _failed_span(new_trace_id(), attributes)
    )

    assert stored.attributes["provider.ratelimit"] == {
        "anthropic-ratelimit-requests-limit": "50",
        "anthropic-ratelimit-requests-remaining": "0",
    }


async def test_auth_header_names_never_stored(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    headers = {name: "secret-value" for name in AUTH_HEADERS}
    headers.update({name.title(): "secret-value" for name in AUTH_HEADERS})
    headers["Retry-After"] = "12"
    attributes = {"http.status_code": 429, "provider.ratelimit": headers}

    stored = await _only_span(
        client, workspace, session_factory, _failed_span(new_trace_id(), attributes)
    )

    assert stored.attributes["provider.ratelimit"] == {"retry-after": "12"}
    assert "secret-value" not in str(stored.attributes)


@pytest.mark.parametrize(
    "ratelimit",
    [
        "anthropic-ratelimit-tokens-remaining: 0",
        [["anthropic-ratelimit-tokens-remaining", "0"]],
        None,
        {"anthropic-ratelimit-tokens-remaining": {"value": "0"}},
        {"anthropic-ratelimit-tokens-remaining": 0},
        {"x-junk": "1"},
    ],
    ids=["string", "list", "null", "nested object", "number value", "nothing allowlisted"],
)
async def test_non_object_ratelimit_attribute_is_dropped_not_rejected(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    ratelimit: Any,
) -> None:
    attributes = {"http.status_code": 429, "provider.ratelimit": ratelimit, "keep": "me"}

    stored = await _only_span(
        client, workspace, session_factory, _failed_span(new_trace_id(), attributes)
    )

    assert "provider.ratelimit" not in stored.attributes
    assert stored.attributes["keep"] == "me"
    assert stored.error_class == "rate_limit"
    assert stored.rate_limit_kind == "spend_cap"  # from the message alone


async def test_oversized_provider_strings_are_dropped(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    attributes = {
        "http.status_code": 429,
        "provider.error.type": "t" * 65,
        "provider.error.code": "c" * 65,
        "provider.request_id": "r" * 129,
        "provider.retry_after_s": "30",
    }

    stored = await _only_span(
        client,
        workspace,
        session_factory,
        _failed_span(new_trace_id(), attributes, status_message="Too many requests"),
    )

    assert stored.provider_error_type is None
    assert stored.provider_error_code is None
    for key in (
        "provider.error.type",
        "provider.error.code",
        "provider.request_id",
        "provider.retry_after_s",
    ):
        assert key not in stored.attributes
    assert stored.rate_limit_kind == "unknown"


async def test_limits_are_inclusive(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    attributes = {
        "http.status_code": 429,
        "provider.error.type": "t" * 64,
        "provider.error.code": "c" * 64,
        "provider.request_id": "r" * 128,
    }

    stored = await _only_span(
        client, workspace, session_factory, _failed_span(new_trace_id(), attributes)
    )

    assert stored.provider_error_type == "t" * 64
    assert stored.provider_error_code == "c" * 64
    assert stored.attributes["provider.request_id"] == "r" * 128


def _otlp_value(value: Any) -> dict[str, Any]:
    if isinstance(value, int):
        return {"intValue": str(value)}  # proto3 JSON encodes int64 as a string
    return {"stringValue": value}


def _otlp_span(attributes: dict[str, Any], message: str) -> dict[str, Any]:
    now = time.time_ns()
    return {
        "traceId": secrets.token_hex(16),
        "spanId": secrets.token_hex(8),
        "name": "chat claude-haiku-4-5",
        "startTimeUnixNano": str(now - 1_000_000),
        "endTimeUnixNano": str(now),
        "status": {"code": 2, "message": message},
        "attributes": [
            {"key": key, "value": _otlp_value(value)} for key, value in attributes.items()
        ],
    }


async def _ingest_otlp(
    client: httpx.AsyncClient, workspace: Workspace, otlp_span: dict[str, Any]
) -> None:
    document = {"resourceSpans": [{"scopeSpans": [{"spans": [otlp_span]}]}]}
    response = await client.post(
        "/v1/otlp/traces", json=document, headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200, response.text


async def test_otlp_span_gets_kind_from_message_only(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    attributes = {
        "gen_ai.system": "Anthropic",
        "gen_ai.request.model": "claude-haiku-4-5",
        "http.status_code": 429,
        "error.type": "rate_limit_error",
    }
    message = "This request would exceed the rate limit of 1,000 requests per minute"

    await _ingest_otlp(client, workspace, _otlp_span(attributes, message))

    (stored,) = await stored_spans(session_factory)
    assert stored.error_class == "rate_limit"
    assert stored.rate_limit_kind == "requests"
    assert stored.provider_error_type is None
    assert stored.provider_error_code is None


async def test_otlp_client_set_provider_attributes_are_honoured_and_capped(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    attributes = {
        "gen_ai.system": "anthropic",
        "http.status_code": 429,
        "provider.error.type": "rate_limit_error",
        "provider.error.code": "c" * 65,
    }

    await _ingest_otlp(client, workspace, _otlp_span(attributes, "Too many requests"))

    (stored,) = await stored_spans(session_factory)
    assert stored.provider_error_type == "rate_limit_error"
    assert stored.provider_error_code is None
    assert "provider.error.code" not in stored.attributes


async def test_success_span_with_spoofed_header_has_no_kind(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    attributes = {
        "http.status_code": 200,
        "provider.error.type": "rate_limit_error",
        "provider.error.code": "rate_limit_exceeded",
        "provider.ratelimit": {"x-ratelimit-remaining-tokens": "0"},
    }

    stored = await _only_span(
        client, workspace, session_factory, span(trace_id=new_trace_id(), attributes=attributes)
    )

    assert stored.error_class is None
    assert stored.rate_limit_kind is None
    assert stored.provider_error_type is None
    assert stored.provider_error_code is None


async def test_replay_recomputes_columns(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    trace_id = new_trace_id()
    batch = [
        _failed_span(trace_id, dict(SPEND_CAP_ATTRIBUTES)),
        _failed_span(
            trace_id,
            {"http.status_code": 429},
            status_message="This request would exceed 10,000 input tokens per minute",
        ),
    ]

    first = await ingest_native(client, workspace, batch)
    rows_once = await telemetry_rows(session_factory, workspace.project_id)
    second = await ingest_native(client, workspace, batch)
    rows_twice = await telemetry_rows(session_factory, workspace.project_id)

    assert first.status_code == second.status_code == 200
    assert rows_once == rows_twice
    span_rows, _ = rows_twice
    assert sorted(row["rate_limit_kind"] for row in span_rows) == ["spend_cap", "tokens"]


async def test_trace_detail_exposes_the_three_fields(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    trace_id = new_trace_id()
    ok = span(trace_id=trace_id)
    response = await ingest_native(
        client, workspace, [_failed_span(trace_id, dict(SPEND_CAP_ATTRIBUTES)), ok]
    )
    assert response.status_code == 200, response.text

    detail = await workspace.owner.get(f"/api/v1/projects/{workspace.project_id}/traces/{trace_id}")

    assert detail.status_code == 200, detail.text
    by_status = {item["status"]: item for item in detail.json()["spans"]}
    failed, succeeded = by_status["error"], by_status["ok"]
    assert failed["provider_error_type"] == "rate_limit_error"
    assert failed["provider_error_code"] == "enforced_spend_limit_reached"
    assert failed["rate_limit_kind"] == "spend_cap"
    assert succeeded["provider_error_type"] is None
    assert succeeded["provider_error_code"] is None
    assert succeeded["rate_limit_kind"] is None


async def test_sdk_marked_gateway_refusal_is_rate_limit_kind_gateway(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # What the Python SDK sends when the gateway's own key limit refused the call: the gateway
    # code and the status, with no provider fields, and a message that alone would read as an
    # OpenAI requests-per-minute limit.
    body = span(
        trace_id=new_trace_id(),
        name="chat gpt-4o-mini",
        provider="openai",
        model="gpt-4o-mini",
        status="error",
        status_message="Rate limit reached for gateway key spl_gw_ab12: 60 requests per minute.",
        attributes={"http.status_code": 429, "spanlight.gateway.error_code": "RATE_LIMITED"},
    )

    stored = await _only_span(client, workspace, session_factory, body)

    assert stored.error_class == "rate_limit"
    assert stored.rate_limit_kind == "gateway"
    assert stored.provider_error_type is None
    assert stored.provider_error_code is None

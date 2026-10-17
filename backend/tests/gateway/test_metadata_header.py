"""The `x-spanlight-metadata` and `x-spanlight-trace-name` gateway headers (spec §4.7, §8.3).

The metadata header is one JSON object of at most 8,192 bytes. A header that is too big, not
JSON, too deep or not an object is dropped whole and counted (`reason="header"`); an invalid
entry is dropped on its own. Neither header is forwarded upstream. The trace-name header names
the trace, even one the call joins.
"""

import json
import logging
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.observability import METADATA_DROPPED
from app.gateway import tracing
from app.gateway.context import GatewayRequest
from app.gateway.trace_headers import (
    MAX_METADATA_HEADER_BYTES,
    METADATA_HEADER,
    TRACE_NAME_HEADER,
    TraceHeaders,
)
from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import (
    FakeProvider,
    fixture_text,
    logged,
    metric_delta,
    read_gateway_traces,
)
from tests.helpers import bearer, create_api_key, new_trace_id, span

CHAT_BODY = {"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "Hello"}]}
SECRET_VALUE = "acme-secret-value"


def _headers(metadata: str) -> TraceHeaders:
    return TraceHeaders.from_headers({METADATA_HEADER: metadata})


def _header_drops() -> Any:
    return metric_delta(METADATA_DROPPED, path="gateway", reason="header")


async def _call(
    workspace: GatewayWorkspace, provider: FakeProvider, headers: dict[str, str]
) -> None:
    provider.reply_json(fixture_text("openai/chat.json").encode())
    response = await workspace.gw.post("/gw/v1/chat/completions", json=CHAT_BODY, headers=headers)
    assert response.status_code == 200, response.text


async def _sdk_trace(workspace: GatewayWorkspace, trace: dict[str, Any]) -> dict[str, Any]:
    """A root span sent through `POST /v1/traces`, as an SDK would, on a new trace."""
    key = await create_api_key(workspace.owner, str(workspace.project_id))
    root = span(trace_id=new_trace_id(), trace=trace)
    sent = await workspace.gw.post(
        "/v1/traces", json={"spans": [root]}, headers=bearer(key["secret"])
    )
    assert sent.status_code == 200, sent.text
    return root


def _joining(root: dict[str, Any]) -> dict[str, str]:
    return {"x-spanlight-trace-id": root["trace_id"], "x-spanlight-parent-span-id": root["span_id"]}


# Parsing the header.


def test_a_valid_header_is_parsed_into_string_values() -> None:
    parsed = _headers('{"customer_id": "acme", "plan.tier": 3, "beta": true}')

    assert parsed.metadata == {"customer_id": "acme", "plan.tier": "3", "beta": "true"}


def test_no_header_means_no_metadata() -> None:
    assert TraceHeaders.from_headers({}).metadata == {}
    assert TraceHeaders().metadata == {}


def test_the_header_name_is_matched_in_any_case() -> None:
    parsed = TraceHeaders.from_headers({"X-Spanlight-Metadata": '{"a": "b"}'})

    assert parsed.metadata == {"a": "b"}


def test_header_over_8_kib_is_dropped_whole_and_counted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    padding = "x" * MAX_METADATA_HEADER_BYTES
    too_big = json.dumps({"customer_id": SECRET_VALUE, "pad": padding})
    caplog.set_level(logging.DEBUG)
    dropped = _header_drops()

    parsed = _headers(too_big)

    assert parsed.metadata == {}
    assert dropped.value == 1
    [event] = logged(caplog, "metadata_header_dropped")
    assert event["reason"] == "too_large"
    assert SECRET_VALUE not in json.dumps(event, default=str)


def test_a_header_of_exactly_8_kib_is_kept() -> None:
    prefix, suffix = '{"k": "', '"}'
    filler = "v" * (MAX_METADATA_HEADER_BYTES - len(prefix) - len(suffix))
    header = prefix + filler + suffix
    assert len(header.encode()) == MAX_METADATA_HEADER_BYTES
    dropped = _header_drops()

    parsed = _headers(header)

    # The one value is longer than 256 characters, so it is dropped on its own; the header is not.
    assert parsed.metadata == {}
    assert dropped.value == 0


@pytest.mark.parametrize(
    ("header", "reason"),
    [
        ("[1, 2]", "not_object"),
        ('"customer_id=acme"', "not_object"),
        ("null", "not_object"),
        ("{customer_id: acme}", "invalid_json"),
        ('{"a": "b"', "invalid_json"),
        ("", "invalid_json"),
        ('{"a": ' + "[" * 4000 + "]" * 4000 + "}", "too_deep"),
        ('{"a": ' + "[" * 70 + "]" * 70 + "}", "too_deep"),
    ],
)
def test_non_object_json_is_dropped_whole(
    header: str, reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    dropped = _header_drops()

    parsed = _headers(header)

    assert parsed.metadata == {}
    assert dropped.value == 1
    [event] = logged(caplog, "metadata_header_dropped")
    assert event["reason"] == reason


def test_invalid_keys_are_dropped_singly() -> None:
    header = json.dumps(
        {
            "customer_id": "acme",
            "Customer": "upper case key",
            "has space": "x",
            "nested": {"a": 1},
            "empty": "",
            "gone": None,
        }
    )
    drops = {
        reason: metric_delta(METADATA_DROPPED, path="gateway", reason=reason)
        for reason in ("key", "type", "value", "header")
    }

    parsed = _headers(header)

    assert parsed.metadata == {"customer_id": "acme"}
    assert {reason: delta.value for reason, delta in drops.items()} == {
        "key": 2,
        "type": 1,
        "value": 1,
        "header": 0,
    }


def test_only_the_first_32_keys_in_lexical_order_are_kept() -> None:
    header = json.dumps({f"k{index:02d}": "v" for index in range(40)})
    counted = metric_delta(METADATA_DROPPED, path="gateway", reason="count")

    parsed = _headers(header)

    assert list(parsed.metadata) == [f"k{index:02d}" for index in range(32)]
    assert counted.value == 8


def test_unicode_escapes_are_restored_and_nul_is_stripped() -> None:
    # `json.dumps` writes ASCII only: the values arrive as `\uXXXX` escapes.
    header = json.dumps({"city": "Zürich", "team": "数据", "note": "a\u0000b"})
    assert header.isascii()

    parsed = _headers(header)

    assert parsed.metadata == {"city": "Zürich", "team": "数据", "note": "ab"}


# Naming the trace.


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("  checkout flow  ", "checkout flow"),
        ("n" * 256, "n" * 256),
        ("n" * 257, None),
        ("   ", None),
        ("", None),
    ],
)
def test_the_trace_name_header_is_trimmed_and_bounded(value: str, expected: str | None) -> None:
    assert TraceHeaders.from_headers({TRACE_NAME_HEADER: value}).trace_name == expected


def _trace_payload(trace: TraceHeaders) -> dict[str, Any]:
    request = GatewayRequest(surface="chat_completions", body=CHAT_BODY, stream=False, trace=trace)
    result = cast(Any, SimpleNamespace(fault=None))
    context = cast(Any, SimpleNamespace(environment="test", default_tags=[]))
    return tracing._trace(request, result, context, "chat_completions gpt-4.1-mini")


def test_the_trace_payload_carries_the_header_metadata() -> None:
    payload = _trace_payload(_headers('{"customer_id": "acme"}'))

    assert payload["metadata"] == {"customer_id": "acme"}


def test_the_trace_payload_names_the_trace_from_the_header_even_when_joining() -> None:
    joined = "a" * 32
    named = TraceHeaders.from_headers({"x-spanlight-trace-id": joined, TRACE_NAME_HEADER: "run"})
    unnamed = TraceHeaders.from_headers({"x-spanlight-trace-id": joined})

    assert _trace_payload(named)["name"] == "run"
    assert _trace_payload(unnamed)["name"] is None
    assert _trace_payload(TraceHeaders())["name"] == "chat_completions gpt-4.1-mini"
    assert _trace_payload(TraceHeaders(trace_name="run"))["name"] == "run"


def test_a_call_whose_body_names_no_model_names_its_trace_like_its_span() -> None:
    # A call refused before its body was read: the span's name is the fallback.
    unread = GatewayRequest(surface="chat_completions", body={}, stream=False)
    requested = GatewayRequest(surface="chat_completions", body=CHAT_BODY, stream=False)

    assert tracing.default_trace_name(unread, "chat_completions gpt-4.1-mini-2025-04-14") == (
        "chat_completions gpt-4.1-mini-2025-04-14"
    )
    assert tracing.default_trace_name(unread) == "chat_completions"
    assert tracing.default_trace_name(requested, "chat_completions gpt-4.1-mini-2025-04-14") == (
        "chat_completions gpt-4.1-mini"
    )


# End to end, through `/gw/v1`.


async def test_header_is_not_forwarded_upstream(
    gateway_workspace: GatewayWorkspace, fake_provider: FakeProvider
) -> None:
    headers = {METADATA_HEADER: '{"customer_id": "acme"}', TRACE_NAME_HEADER: "checkout"}

    await _call(gateway_workspace, fake_provider, headers)

    [sent] = fake_provider.requests
    names = {name.lower() for name in sent.headers}
    assert METADATA_HEADER not in names
    assert TRACE_NAME_HEADER not in names
    assert not [name for name in names if name.startswith("x-spanlight-")]


async def test_a_dropped_header_does_not_fail_the_call(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    dropped = _header_drops()

    await _call(gateway_workspace, fake_provider, {METADATA_HEADER: "not json"})

    assert dropped.value == 1
    [trace] = await read_gateway_traces(session_factory, gateway_workspace.project_id)
    assert trace["name"] == "chat_completions gpt-4.1-mini"  # the requested model, not the echo


async def test_trace_name_header_names_a_new_and_a_joined_trace(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    root = await _sdk_trace(gateway_workspace, {"name": "sdk name"})

    await _call(gateway_workspace, fake_provider, {TRACE_NAME_HEADER: " new trace "})
    await _call(
        gateway_workspace, fake_provider, {**_joining(root), TRACE_NAME_HEADER: "joined run"}
    )

    traces = await read_gateway_traces(session_factory, gateway_workspace.project_id)
    names = {trace["trace_id"]: trace["name"] for trace in traces}
    assert names[root["trace_id"]] == "joined run"
    assert sorted(names.values()) == ["joined run", "new trace"]


async def test_header_metadata_is_stored_on_a_new_trace(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    header = json.dumps({"customer_id": "acme", "plan": "pro", "Bad Key": "x"})

    await _call(gateway_workspace, fake_provider, {METADATA_HEADER: header})

    [trace] = await read_gateway_traces(session_factory, gateway_workspace.project_id)
    assert trace["metadata"] == {"customer_id": "acme", "plan": "pro"}


async def test_header_metadata_merges_into_a_joined_trace(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    root = await _sdk_trace(
        gateway_workspace, {"name": "sdk", "metadata": {"customer_id": "acme", "region": "eu"}}
    )
    header = json.dumps({"region": "us", "plan": "pro"})

    await _call(gateway_workspace, fake_provider, {**_joining(root), METADATA_HEADER: header})

    [trace] = await read_gateway_traces(session_factory, gateway_workspace.project_id)
    assert trace["metadata"] == {"customer_id": "acme", "region": "us", "plan": "pro"}

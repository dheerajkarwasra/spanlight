"""OTLP content edges: unparseable gen_ai messages, numbers jsonb cannot hold, partial success."""

import json
import math
import secrets
import time
from typing import Any

import httpx
import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span as ProtoSpan
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.helpers import Workspace, bearer
from tests.ingest.support import nested_json, otlp_json_document, otlp_json_span, stored_spans


async def test_otlp_span_with_unparseable_gen_ai_messages_is_still_stored(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    too_deep = nested_json(100)
    document = otlp_json_document(
        otlp_json_span(
            {
                "gen_ai.system": "openai",
                "gen_ai.input.messages": "this is not json {",
                "gen_ai.output.messages": too_deep,
            },
            name="chat",
        )
    )
    response = await client.post(
        "/v1/otlp/traces", json=document, headers=bearer(workspace.api_key)
    )

    assert response.status_code == 200, response.text
    assert response.json() == {}
    (stored,) = await stored_spans(session_factory)
    assert stored.kind.value == "llm"
    assert stored.input == "this is not json {"  # kept as the raw string
    assert stored.output == too_deep  # parsed too deep to keep as structure: also kept raw


async def test_otlp_protobuf_partial_success_encodes_rejections(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = time.time_ns()
    request = ExportTraceServiceRequest()
    scope = request.resource_spans.add().scope_spans.add()
    scope.spans.extend(
        [
            ProtoSpan(
                trace_id=secrets.token_bytes(16),
                span_id=secrets.token_bytes(8),
                name="ok",
                start_time_unix_nano=now - 1000,
                end_time_unix_nano=now,
            ),
            ProtoSpan(
                trace_id=bytes(16),
                span_id=secrets.token_bytes(8),
                name="zero trace id",
                start_time_unix_nano=now - 1000,
                end_time_unix_nano=now,
            ),
        ]
    )
    response = await client.post(
        "/v1/otlp/traces",
        content=request.SerializeToString(),
        headers={**bearer(workspace.api_key), "Content-Type": "application/x-protobuf"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-protobuf"
    decoded = ExportTraceServiceResponse.FromString(response.content)
    assert decoded.partial_success.rejected_spans == 1
    assert "trace_id" in decoded.partial_success.error_message
    (stored,) = await stored_spans(session_factory)
    assert stored.name == "ok"


def _json_attribute(key: str, value: dict[str, Any]) -> dict[str, Any]:
    return {"key": key, "value": value}


async def test_otlp_json_non_finite_numbers_are_stored_as_their_names(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # OTLP/JSON spells non-finite doubles as strings; jsonb has no NaN, so they stay strings.
    otlp_span = otlp_json_span({"gen_ai.input.messages": '[{"score": NaN}]'}, name="chat")
    otlp_span["attributes"] += [
        _json_attribute("nan", {"doubleValue": "NaN"}),
        _json_attribute("inf", {"doubleValue": "Infinity"}),
        _json_attribute("ninf", {"doubleValue": "-Infinity"}),
    ]

    response = await client.post(
        "/v1/otlp/traces",
        json=otlp_json_document(otlp_span, otlp_json_span()),
        headers=bearer(workspace.api_key),
    )

    assert response.status_code == 200, response.text
    assert response.json() == {}
    stored = {span.name: span for span in await stored_spans(session_factory)}
    attributes = stored["chat"].attributes
    assert (attributes["nan"], attributes["inf"], attributes["ninf"]) == (
        "NaN",
        "Infinity",
        "-Infinity",
    )
    assert stored["chat"].input == [{"score": "NaN"}]


async def test_otlp_protobuf_non_finite_doubles_are_stored_as_their_names(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = time.time_ns()
    request = ExportTraceServiceRequest()
    request.resource_spans.add().scope_spans.add().spans.append(
        ProtoSpan(
            trace_id=secrets.token_bytes(16),
            span_id=secrets.token_bytes(8),
            name="doubles",
            start_time_unix_nano=now - 1000,
            end_time_unix_nano=now,
            attributes=[
                KeyValue(key="nan", value=AnyValue(double_value=math.nan)),
                KeyValue(key="inf", value=AnyValue(double_value=math.inf)),
                KeyValue(key="half", value=AnyValue(double_value=0.5)),
            ],
        )
    )

    response = await client.post(
        "/v1/otlp/traces",
        content=request.SerializeToString(),
        headers={**bearer(workspace.api_key), "Content-Type": "application/x-protobuf"},
    )

    assert response.status_code == 200, response.text
    (stored,) = await stored_spans(session_factory)
    assert {key: stored.attributes[key] for key in ("nan", "inf", "half")} == {
        "nan": "NaN",
        "inf": "Infinity",
        "half": 0.5,
    }


@pytest.mark.parametrize(
    "value",
    [
        pytest.param({"doubleValue": "abc"}, id="double-not-a-number"),
        pytest.param({"doubleValue": {}}, id="double-an-object"),
        pytest.param({"intValue": "²"}, id="int-superscript-digit"),
        pytest.param({"intValue": "--5"}, id="int-two-signs"),
        pytest.param({"intValue": "9" * 5000}, id="int-5000-digits"),
    ],
)
async def test_otlp_json_malformed_number_is_400_not_500(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
    value: dict[str, Any],
) -> None:
    otlp_span = otlp_json_span()
    otlp_span["attributes"] = [_json_attribute("n", value)]

    response = await client.post(
        "/v1/otlp/traces", json=otlp_json_document(otlp_span), headers=bearer(workspace.api_key)
    )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "INVALID_OTLP"
    assert await stored_spans(session_factory) == []


async def test_otlp_json_number_literal_past_a_double_is_invalid_json(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # `1e999` as a JSON number (not the string "Infinity") makes the body itself unusable.
    otlp_span = otlp_json_span()
    otlp_span["attributes"] = [_json_attribute("n", {"doubleValue": "<number>"})]
    body = json.dumps(otlp_json_document(otlp_span)).replace('"<number>"', "1e999")

    response = await client.post(
        "/v1/otlp/traces",
        content=body,
        headers={**bearer(workspace.api_key), "Content-Type": "application/json"},
    )

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "INVALID_JSON"
    assert await stored_spans(session_factory) == []


async def test_otlp_gen_ai_messages_with_an_unconvertible_number_are_kept_raw(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    messages = '[{"n": ' + "9" * 5000 + "}]"
    document = otlp_json_document(otlp_json_span({"gen_ai.input.messages": messages}))

    response = await client.post(
        "/v1/otlp/traces", json=document, headers=bearer(workspace.api_key)
    )

    assert response.status_code == 200, response.text
    (stored,) = await stored_spans(session_factory)
    assert stored.input == messages


async def test_otlp_gen_ai_messages_with_a_number_past_a_double_store_it_as_infinity(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    messages = '[{"big": 1e999, "small": -1e400, "fine": 1.5}]'
    document = otlp_json_document(otlp_json_span({"gen_ai.input.messages": messages}))

    response = await client.post(
        "/v1/otlp/traces", json=document, headers=bearer(workspace.api_key)
    )

    assert response.status_code == 200, response.text
    (stored,) = await stored_spans(session_factory)
    assert stored.input == [{"big": "Infinity", "small": "-Infinity", "fine": 1.5}]

"""Payload edges on ingestion: truncation at the cut and JSON nested too deep."""

import json
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ingest.normalize import MAX_ATTRIBUTES_BYTES, MAX_PAYLOAD_BYTES
from tests.helpers import Workspace, bearer, new_trace_id, span
from tests.ingest.support import ingest_native, nested_json, stored_spans

SECRET = "sk-ant-api03-abcdefghijklmnop_QRS"


# --- truncation at the cut ----------------------------------------------------


async def test_input_truncation_keeps_the_output_and_stays_within_the_limit(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    output = {"role": "assistant", "content": "short answer"}
    response = await ingest_native(
        client,
        workspace,
        [span(trace_id=new_trace_id(), input="x" * (MAX_PAYLOAD_BYTES + 50_000), output=output)],
    )

    assert response.json() == {"accepted": 1, "rejected": []}
    (stored,) = await stored_spans(session_factory)
    assert stored.truncated is True
    assert isinstance(stored.input, str)
    assert len(stored.input.encode()) <= MAX_PAYLOAD_BYTES
    assert stored.output == output


async def test_a_200_kb_rag_context_is_stored_whole_with_its_size(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    context = "retrieved passage about the refund policy. " * 4_700  # about 200 KB
    payload = [{"role": "user", "content": context}]
    output = {"role": "assistant", "content": "short answer"}

    response = await ingest_native(
        client, workspace, [span(trace_id=new_trace_id(), input=payload, output=output)]
    )

    assert response.json() == {"accepted": 1, "rejected": []}
    (stored,) = await stored_spans(session_factory)
    assert stored.truncated is False
    assert stored.input == payload
    compact = {"ensure_ascii": False, "separators": (",", ":")}
    expected = len(json.dumps(payload, **compact).encode()) + len(
        json.dumps(output, **compact).encode()
    )
    assert stored.payload_bytes == expected


async def test_a_secret_under_the_cut_leaves_no_fragment(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # The secret starts before the cut and ends after it. Redacting after the cut would keep its
    # first characters, which no pattern would match any more.
    prefix = "a" * (MAX_PAYLOAD_BYTES - 10)
    text = f"{prefix} {SECRET} {'b' * 1000}"
    assert text.index(SECRET) < MAX_PAYLOAD_BYTES < text.index(SECRET) + len(SECRET)

    await ingest_native(client, workspace, [span(trace_id=new_trace_id(), input=text)])

    (stored,) = await stored_spans(session_factory)
    assert stored.truncated is True
    assert isinstance(stored.input, str)
    assert "sk-ant" not in stored.input
    assert "api03" not in stored.input
    assert len(stored.input.encode()) <= MAX_PAYLOAD_BYTES


async def test_a_multibyte_character_across_the_cut_is_dropped_whole(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # The opening quote takes one byte, so this puts the cut two bytes into a four-byte character.
    text = "a" * (MAX_PAYLOAD_BYTES - 3) + "\U0001f600" * 10

    response = await ingest_native(client, workspace, [span(trace_id=new_trace_id(), input=text)])

    assert response.json() == {"accepted": 1, "rejected": []}
    (stored,) = await stored_spans(session_factory)
    assert stored.truncated is True
    assert isinstance(stored.input, str)
    stored.input.encode()  # valid, so the row could be written and can be read back
    assert "�" not in stored.input
    assert stored.input == '"' + "a" * (MAX_PAYLOAD_BYTES - 3)  # the preview is JSON text


async def test_attributes_over_the_limit_reject_that_span_only(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    trace_id = new_trace_id()
    heavy = span(trace_id=trace_id, attributes={"blob": "z" * (MAX_ATTRIBUTES_BYTES + 1)})
    response = await ingest_native(
        client, workspace, [span(trace_id=trace_id), heavy, span(trace_id=trace_id)]
    )

    body = response.json()
    assert response.status_code == 200
    assert body["accepted"] == 2
    (rejection,) = body["rejected"]
    assert rejection["index"] == 1
    assert rejection["span_id"] == heavy["span_id"]
    assert rejection["reason"].startswith("attributes: exceed")


# --- deep JSON ----------------------------------------------------------------


async def test_deeply_nested_json_is_400_and_stores_nothing(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    headers = {**bearer(workspace.api_key), "Content-Type": "application/json"}
    valid = json.dumps(span(trace_id=new_trace_id()))
    bodies = {
        "as a span": f'{{"spans": [{nested_json(100)}]}}',
        "in a span's input": f'{{"spans": [{valid[:-1]}, "input": {nested_json(100)}}}]}}',
        # Past the parser's own recursion limit, which raises RecursionError.
        "past the parser's limit": nested_json(200_000),
    }

    for label, body in bodies.items():
        response = await client.post("/v1/traces", content=body, headers=headers)
        assert response.status_code == 400, label
        assert response.json()["code"] == "INVALID_JSON", label
        assert "nested" in response.json()["detail"], label
    assert await stored_spans(session_factory) == []


async def test_json_at_the_depth_limit_is_accepted_and_one_level_over_is_400(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # {"spans": [ {"input": [[...]] } ]}: the envelope and the span take three levels of 64.
    def nested(levels: int) -> Any:
        value: Any = "leaf"
        for _ in range(levels):
            value = [value]
        return value

    at_limit = await ingest_native(
        client, workspace, [span(trace_id=new_trace_id(), input=nested(61))]
    )
    assert at_limit.json() == {"accepted": 1, "rejected": []}

    over = await ingest_native(client, workspace, [span(trace_id=new_trace_id(), input=nested(62))])
    assert over.status_code == 400
    assert over.json()["code"] == "INVALID_JSON"
    assert len(await stored_spans(session_factory)) == 1


async def test_otlp_json_nested_too_deep_is_400(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    response = await client.post(
        "/v1/otlp/traces",
        content=f'{{"resourceSpans": {nested_json(100)}}}',
        headers={**bearer(workspace.api_key), "Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_JSON"

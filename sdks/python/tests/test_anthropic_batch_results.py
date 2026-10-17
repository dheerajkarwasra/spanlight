"""``wrap_anthropic`` records Message Batch results read through ``messages.batches.results``.

The real ``anthropic`` SDK runs on a mocked transport: ``results`` first retrieves the batch
(``GET /v1/messages/batches/{id}``), then streams the JSONL file at its ``results_url``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import anthropic
from provider_mocks import MockProvider, provider_httpx
from support import FakeIngestApi, flushed_spans

import spanlight
from spanlight.integrations._batch import batch_span_id, batch_trace_id

API_KEY = "sk-ant-api03-test-secret"
BATCH_ID = "msgbatch_01HkcTjaV5uDC8jWR4ZsDV8d"
MODEL = "claude-haiku-4-5-20251001"
RESULTS_URL = f"https://api.anthropic.com/v1/messages/batches/{BATCH_ID}/results"

MESSAGE_BATCH = {
    "id": BATCH_ID,
    "type": "message_batch",
    "processing_status": "ended",
    "request_counts": {"processing": 0, "succeeded": 1, "errored": 1, "canceled": 0, "expired": 1},
    "ended_at": "2026-10-15T18:37:24.100435Z",
    "created_at": "2026-10-15T18:37:24.100435Z",
    "expires_at": "2026-10-16T18:37:24.100435Z",
    "archived_at": None,
    "cancel_initiated_at": None,
    "results_url": RESULTS_URL,
}

# One line per request, as the Batches API writes them. The provider's own tier says
# "standard" here: the Batches API bills every request at the batch rate regardless.
SUCCEEDED = {
    "custom_id": "refund-question-1",
    "result": {
        "type": "succeeded",
        "message": {
            "id": "msg_014VwiXbi91y3JMjcpyGBHX5",
            "type": "message",
            "role": "assistant",
            "model": MODEL,
            "content": [{"type": "text", "text": "Refunds take 5-7 business days."}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 12,
                "cache_read_input_tokens": 1500,
                "cache_creation_input_tokens": 0,
                "output_tokens": 9,
                "service_tier": "standard",
                "server_tool_use": {"web_search_requests": 2},
            },
        },
    },
}
ERRORED = {
    "custom_id": "refund-question-2",
    "result": {
        "type": "errored",
        "error": {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "max_tokens: Field required",
            },
        },
    },
}
EXPIRED = {"custom_id": "refund-question-3", "result": {"type": "expired"}}


def respond_with(lines: list[dict[str, Any]]) -> MockProvider:
    body = "".join(json.dumps(line) + "\n" for line in lines).encode()

    def respond(request: Any) -> Any:
        if request.url.path.endswith("/results"):
            return provider_httpx.Response(
                200, content=body, headers={"content-type": "application/binary"}
            )
        return provider_httpx.Response(200, json=MESSAGE_BATCH)

    return MockProvider(respond)


def make_client(provider: MockProvider, tracer: spanlight.Spanlight) -> anthropic.Anthropic:
    client = anthropic.Anthropic(api_key=API_KEY, http_client=provider.sync_client(), max_retries=0)
    return spanlight.wrap_anthropic(client, spanlight=tracer)


def make_async_client(
    provider: MockProvider, tracer: spanlight.Spanlight
) -> anthropic.AsyncAnthropic:
    client = anthropic.AsyncAnthropic(
        api_key=API_KEY, http_client=provider.async_client(), max_retries=0
    )
    return spanlight.wrap_anthropic(client, spanlight=tracer)


def spans_by_custom_id(spans: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The batch spans among ``spans``, keyed by the request's ``custom_id``."""
    return {
        span["attributes"]["batch.custom_id"]: span
        for span in spans
        if "batch.custom_id" in span["attributes"]
    }


def test_succeeded_item_becomes_batch_span(tracer, ingest):
    client = make_client(respond_with([SUCCEEDED]), tracer)

    with tracer.span("unrelated-parent"):
        items = list(client.messages.batches.results(BATCH_ID))

    assert [item.custom_id for item in items] == ["refund-question-1"]
    span = spans_by_custom_id(flushed_spans(tracer, ingest))["refund-question-1"]

    expected_trace = hashlib.sha256(f"spanlight:batch:{BATCH_ID}".encode()).hexdigest()[:32]
    expected_span = hashlib.sha256(f"{BATCH_ID}:refund-question-1".encode()).hexdigest()[:16]
    assert span["trace_id"] == expected_trace == batch_trace_id(BATCH_ID)
    assert span["span_id"] == expected_span == batch_span_id(BATCH_ID, "refund-question-1")
    # The batch trace stands alone: the span the results were read in is not its parent.
    assert span["parent_span_id"] is None
    assert span["name"] == f"batch {MODEL}"
    assert span["kind"] == "llm"
    assert span["status"] == "ok"
    assert span["provider"] == "anthropic"
    assert span["model"] == MODEL
    assert span["usage"] == {
        "input_tokens": 1512,
        "output_tokens": 9,
        "cached_tokens": 1500,
        "cache_write_tokens": 0,
        "service_tier": "batch",
        "server_tool_use": {"web_search_requests": 2},
    }
    assert span["finish_reason"] == "end_turn"
    assert span["input"] is None
    assert span["output"] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "Refunds take 5-7 business days."}],
    }
    assert span["attributes"] == {
        "batch.id": BATCH_ID,
        "batch.custom_id": "refund-question-1",
        "batch.result_type": "succeeded",
        "batch.request_available": False,
        "timing.synthetic": True,
        "usage.service_tier_raw": "standard",
    }
    assert span["start_time"] == span["end_time"]
    assert span["trace"]["name"] == f"batch {BATCH_ID}"
    assert span["trace"]["tags"] == ["spanlight:batch"]
    assert span["trace"]["environment"] == "test"
    assert API_KEY not in json.dumps(span)


def test_error_items_become_error_spans_without_usage(tracer, ingest):
    client = make_client(respond_with([ERRORED, EXPIRED]), tracer)

    items = list(client.messages.batches.results(message_batch_id=BATCH_ID))

    assert [item.result.type for item in items] == ["errored", "expired"]
    spans = spans_by_custom_id(flushed_spans(tracer, ingest))

    errored = spans["refund-question-2"]
    assert errored["status"] == "error"
    assert errored["status_message"] == "max_tokens: Field required"
    assert errored["attributes"]["error.type"] == "invalid_request_error"
    assert errored["attributes"]["batch.result_type"] == "errored"
    assert errored["usage"] is None
    assert errored["output"] is None
    assert errored["provider"] == "anthropic"
    assert errored["kind"] == "llm"
    assert errored["span_id"] == batch_span_id(BATCH_ID, "refund-question-2")
    assert errored["trace_id"] == batch_trace_id(BATCH_ID)
    assert errored["start_time"] == errored["end_time"]

    expired = spans["refund-question-3"]
    assert expired["status"] == "error"
    assert expired["status_message"] == "expired"
    assert expired["attributes"]["error.type"] == "expired"
    assert expired["attributes"]["batch.result_type"] == "expired"
    assert expired["attributes"]["timing.synthetic"] is True
    assert expired["usage"] is None


def test_ids_are_deterministic_and_replay_is_idempotent(tracer, ingest: FakeIngestApi):
    client = make_client(respond_with([SUCCEEDED, ERRORED, EXPIRED]), tracer)

    for _ in range(2):
        for _item in client.messages.batches.results(BATCH_ID):
            pass
        assert tracer.flush(timeout=5)

    first, second = ingest.batches
    assert len(first) == 3

    def without_times(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            ({k: v for k, v in s.items() if k not in ("start_time", "end_time")} for s in spans),
            key=lambda s: s["span_id"],
        )

    assert without_times(first) == without_times(second)
    assert {s["trace_id"] for s in first} == {batch_trace_id(BATCH_ID)}


async def test_async_results(tracer, ingest):
    client = make_async_client(respond_with([SUCCEEDED, EXPIRED]), tracer)

    decoder = await client.messages.batches.results(BATCH_ID)
    custom_ids = [item.custom_id async for item in decoder]

    assert custom_ids == ["refund-question-1", "refund-question-3"]
    spans = spans_by_custom_id(flushed_spans(tracer, ingest))
    assert spans["refund-question-1"]["usage"]["service_tier"] == "batch"
    assert spans["refund-question-1"]["span_id"] == batch_span_id(BATCH_ID, "refund-question-1")
    assert spans["refund-question-3"]["status"] == "error"


def test_unreadable_item_is_skipped_and_iteration_continues(tracer, ingest):
    client = make_client(respond_with([{"result": {"type": "succeeded"}}, EXPIRED]), tracer)

    items = list(client.messages.batches.results(BATCH_ID))

    assert len(items) == 2
    spans = flushed_spans(tracer, ingest)
    assert [s["attributes"]["batch.custom_id"] for s in spans] == ["refund-question-3"]

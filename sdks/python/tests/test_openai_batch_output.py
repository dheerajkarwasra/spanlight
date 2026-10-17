"""``spanlight.record_openai_batch_output`` records an OpenAI Batch output file as spans.

Each JSONL line is ``{id, custom_id, response: {status_code, request_id, body}, error}``,
as the Batch API writes its output and error files.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from provider_mocks import OPENAI_CHAT_COMPLETION, openai_response_object
from support import flushed_spans

import spanlight
from spanlight.integrations._batch import batch_span_id, batch_trace_id

BATCH_ID = "batch_67ccd2bed1ec8190b14f964abc054267"


def output_line(custom_id: str, body: dict[str, Any], status_code: int = 200) -> str:
    return json.dumps(
        {
            "id": f"batch_req_{custom_id}",
            "custom_id": custom_id,
            "response": {"status_code": status_code, "request_id": "req_1", "body": body},
            "error": None,
        }
    )


CHAT_LINE = output_line("chat-1", OPENAI_CHAT_COMPLETION)
RESPONSES_LINE = output_line("responses-1", openai_response_object())
BAD_REQUEST_LINE = output_line(
    "chat-2",
    {
        "error": {
            "message": "Invalid value for 'model': 'gpt-4o-mini-typo'.",
            "type": "invalid_request_error",
            "param": "model",
            "code": "model_not_found",
        }
    },
    status_code=400,
)
FAILED_RESPONSE_LINE = output_line(
    "responses-2",
    {
        **openai_response_object(status="failed"),
        "output": [],
        "error": {"code": "server_error", "message": "The model failed to generate a response."},
    },
)
INCOMPLETE_RESPONSE_LINE = output_line(
    "responses-3",
    {
        **openai_response_object(status="incomplete"),
        "incomplete_details": {"reason": "max_output_tokens"},
    },
)
EXPIRED_LINE = json.dumps(
    {
        "id": "batch_req_chat-3",
        "custom_id": "chat-3",
        "response": None,
        "error": {
            "code": "batch_expired",
            "message": "This request could not be executed before the completion window expired.",
        },
    }
)


def by_custom_id(spans: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {span["attributes"]["batch.custom_id"]: span for span in spans}


def test_batch_output_chat_and_responses_lines(tracer, ingest):
    recorded = spanlight.record_openai_batch_output(
        [CHAT_LINE, RESPONSES_LINE.encode(), ""], batch_id=BATCH_ID, spanlight=tracer
    )

    assert recorded == 2
    spans = by_custom_id(flushed_spans(tracer, ingest))

    chat = spans["chat-1"]
    assert chat["trace_id"] == batch_trace_id(BATCH_ID)
    assert chat["span_id"] == batch_span_id(BATCH_ID, "chat-1")
    assert chat["name"] == "batch gpt-4o-mini-2024-07-18"
    assert chat["kind"] == "llm"
    assert chat["status"] == "ok"
    assert chat["provider"] == "openai"
    assert chat["usage"] == {
        "input_tokens": 1243,
        "output_tokens": 11,
        "cached_tokens": 1024,
        "service_tier": "batch",
    }
    assert chat["finish_reason"] == "stop"
    assert chat["output"]["content"] == "You can request a refund within 30 days."
    assert chat["input"] is None
    assert chat["attributes"] == {
        "batch.id": BATCH_ID,
        "batch.custom_id": "chat-1",
        "batch.result_type": "succeeded",
        "batch.request_available": False,
        "timing.synthetic": True,
        "usage.service_tier_raw": "default",
    }
    assert chat["trace"]["tags"] == ["spanlight:batch"]

    responses = spans["responses-1"]
    assert responses["span_id"] == batch_span_id(BATCH_ID, "responses-1")
    assert responses["model"] == "gpt-4.1-2025-04-14"
    assert responses["usage"] == {
        "input_tokens": 36,
        "output_tokens": 6,
        "cached_tokens": 8,
        "service_tier": "batch",
    }
    assert responses["output"] == {"role": "assistant", "content": "Your order has shipped."}


def test_batch_output_is_idempotent(tracer, ingest):
    for _ in range(2):
        spanlight.record_openai_batch_output([CHAT_LINE], batch_id=BATCH_ID, spanlight=tracer)
        assert tracer.flush(timeout=5)

    first, second = ingest.batches
    assert [s["span_id"] for s in first] == [s["span_id"] for s in second]
    assert first[0]["usage"] == second[0]["usage"]


def test_batch_output_error_line(tracer, ingest):
    recorded = spanlight.record_openai_batch_output(
        [BAD_REQUEST_LINE, EXPIRED_LINE], batch_id=BATCH_ID, spanlight=tracer
    )

    assert recorded == 2
    spans = by_custom_id(flushed_spans(tracer, ingest))

    bad_request = spans["chat-2"]
    assert bad_request["status"] == "error"
    assert bad_request["status_message"] == "Invalid value for 'model': 'gpt-4o-mini-typo'."
    assert bad_request["attributes"]["error.type"] == "invalid_request_error"
    assert bad_request["attributes"]["batch.result_type"] == "errored"
    assert bad_request["usage"] is None
    assert bad_request["output"] is None
    assert bad_request["span_id"] == batch_span_id(BATCH_ID, "chat-2")

    expired = spans["chat-3"]
    assert expired["status"] == "error"
    assert expired["attributes"]["error.type"] == "batch_expired"
    assert expired["status_message"].startswith("This request could not be executed")
    assert expired["usage"] is None


def test_batch_output_never_raises(tracer, ingest):
    garbage: list[str | bytes] = [
        "not json",
        b"\xff\xfe",
        "[1, 2]",
        '{"custom_id": 5, "response": {"status_code": 200, "body": {}}}',
        '{"custom_id": "x", "response": "nope"}',
    ]

    assert spanlight.record_openai_batch_output(garbage, batch_id=BATCH_ID, spanlight=tracer) == 0
    assert flushed_spans(tracer, ingest) == []


def test_batch_output_survives_a_failing_iterable(tracer, ingest):
    def lines() -> Iterator[str]:
        yield CHAT_LINE
        raise OSError("connection reset")

    assert spanlight.record_openai_batch_output(lines(), batch_id=BATCH_ID, spanlight=tracer) == 1


def test_batch_output_accepts_a_whole_file_as_one_string(tracer, ingest):
    content = f"{CHAT_LINE}\n{RESPONSES_LINE}\n"

    assert spanlight.record_openai_batch_output(content, batch_id=BATCH_ID, spanlight=tracer) == 2


def test_batch_output_uses_the_global_client(global_tracer, ingest):
    assert spanlight.record_openai_batch_output([CHAT_LINE], batch_id=BATCH_ID) == 1
    assert flushed_spans(global_tracer, ingest)[0]["provider"] == "openai"


def test_batch_output_records_nothing_when_disabled(ingest):
    client = spanlight.Spanlight(enabled=False, transport=ingest.transport())
    try:
        assert (
            spanlight.record_openai_batch_output([CHAT_LINE], batch_id=BATCH_ID, spanlight=client)
            == 0
        )
    finally:
        client.shutdown(timeout=1)


def test_batch_output_failed_response_keeps_reported_usage(tracer, ingest):
    recorded = spanlight.record_openai_batch_output(
        [FAILED_RESPONSE_LINE], batch_id=BATCH_ID, spanlight=tracer
    )

    assert recorded == 1
    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "error"
    assert span["status_message"] == "The model failed to generate a response."
    assert span["attributes"]["error.type"] == "server_error"
    assert span["attributes"]["batch.result_type"] == "errored"
    # The provider bills the usage it reports, even for a failed response.
    assert span["usage"] == {
        "input_tokens": 36,
        "output_tokens": 6,
        "cached_tokens": 8,
        "service_tier": "batch",
    }
    assert span["model"] == "gpt-4.1-2025-04-14"


def test_batch_output_incomplete_response_is_a_normal_span(tracer, ingest):
    recorded = spanlight.record_openai_batch_output(
        [INCOMPLETE_RESPONSE_LINE], batch_id=BATCH_ID, spanlight=tracer
    )

    assert recorded == 1
    span = flushed_spans(tracer, ingest)[0]
    assert span["status"] == "ok"
    assert span["finish_reason"] == "max_output_tokens"
    assert span["usage"]["output_tokens"] == 6
    assert span["usage"]["service_tier"] == "batch"


ODD_LINES = {
    "choices_not_a_list": output_line("odd", {"model": "gpt-4o-mini", "choices": 5}),
    "output_not_a_list": output_line("odd", {"model": "gpt-4.1", "output": 7}),
    "none_item": None,
    "deeply_nested_json": '{"custom_id": "odd", "response": ' + "[" * 100_000,
}


@pytest.mark.parametrize("odd", ODD_LINES.values(), ids=ODD_LINES.keys())
def test_batch_output_odd_line_does_not_stop_the_rest(tracer, ingest, odd: Any):
    lines: list[Any] = [CHAT_LINE, odd, RESPONSES_LINE]

    assert spanlight.record_openai_batch_output(lines, batch_id=BATCH_ID, spanlight=tracer) == 2
    assert set(by_custom_id(flushed_spans(tracer, ingest))) >= {"chat-1", "responses-1"}

"""Mock provider APIs for driving the real ``openai`` and ``anthropic`` SDKs.

The payloads mirror real API responses (field names, nesting, SSE framing),
so the provider SDKs parse them exactly as they would in production.

Recent provider SDKs ship their own fork of httpx (``httpx2``); older ones use
``httpx``. ``provider_httpx`` is whichever the installed SDKs build on.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

try:
    import httpx2 as provider_httpx
except ImportError:  # pragma: no cover - depends on installed SDK versions
    import httpx as provider_httpx

ProviderRequest = Any
ProviderResponse = Any


class MockProvider:
    """Serves canned responses and records the requests the SDK made."""

    def __init__(self, respond: Callable[[ProviderRequest], ProviderResponse]) -> None:
        self.respond = respond
        self.requests: list[ProviderRequest] = []

    def __call__(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        return self.respond(request)

    def sync_client(self) -> Any:
        return provider_httpx.Client(transport=provider_httpx.MockTransport(self))

    def async_client(self) -> Any:
        return provider_httpx.AsyncClient(transport=provider_httpx.MockTransport(self))


def json_response(body: dict[str, Any], status: int = 200) -> Callable[[Any], Any]:
    def respond(request: Any) -> Any:
        return provider_httpx.Response(
            status,
            json=body,
            headers={"request-id": "req_mock_123", "x-request-id": "req_mock_123"},
        )

    return respond


def sse_response(events: Iterable[tuple[str | None, dict[str, Any] | str]]) -> Callable[[Any], Any]:
    """Build a ``text/event-stream`` response from ``(event_name, data)`` pairs."""
    chunks: list[str] = []
    for event_name, data in events:
        frame = ""
        if event_name is not None:
            frame += f"event: {event_name}\n"
        payload = data if isinstance(data, str) else json.dumps(data)
        frame += f"data: {payload}\n\n"
        chunks.append(frame)
    body = "".join(chunks).encode()

    def respond(request: Any) -> Any:
        return provider_httpx.Response(
            200,
            content=body,
            headers={"content-type": "text/event-stream", "request-id": "req_stream_1"},
        )

    return respond


# ---------------------------------------------------------------------- #
# OpenAI payloads
# ---------------------------------------------------------------------- #

OPENAI_CHAT_COMPLETION = {
    "id": "chatcmpl-B9MBs8CjcvOU2jLn4n570S5qMJKcT",
    "object": "chat.completion",
    "created": 1741569952,
    "model": "gpt-4o-mini-2024-07-18",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "You can request a refund within 30 days.",
                "refusal": None,
                "annotations": [],
            },
            "logprobs": None,
            "finish_reason": "stop",
        }
    ],
    "usage": {
        "prompt_tokens": 1243,
        "completion_tokens": 11,
        "total_tokens": 1254,
        "prompt_tokens_details": {"cached_tokens": 1024, "audio_tokens": 0},
        "completion_tokens_details": {
            "reasoning_tokens": 0,
            "audio_tokens": 0,
            "accepted_prediction_tokens": 0,
            "rejected_prediction_tokens": 0,
        },
    },
    "service_tier": "default",
    "system_fingerprint": "fp_06737a9306",
}


def openai_chunk(
    delta: dict[str, Any] | None,
    *,
    finish_reason: str | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    choices = []
    if delta is not None:
        choices.append(
            {"index": 0, "delta": delta, "logprobs": None, "finish_reason": finish_reason}
        )
    return {
        "id": "chatcmpl-stream-1",
        "object": "chat.completion.chunk",
        "created": 1741569952,
        "model": "gpt-4o-mini-2024-07-18",
        "service_tier": "default",
        "system_fingerprint": "fp_06737a9306",
        "choices": choices,
        "usage": usage,
    }


OPENAI_STREAM_USAGE = {
    "prompt_tokens": 20,
    "completion_tokens": 3,
    "total_tokens": 23,
    "prompt_tokens_details": {"cached_tokens": 0, "audio_tokens": 0},
    "completion_tokens_details": {"reasoning_tokens": 0, "audio_tokens": 0},
}


def openai_text_stream(*, include_usage: bool) -> list[tuple[str | None, dict[str, Any] | str]]:
    events: list[tuple[str | None, dict[str, Any] | str]] = [
        (None, openai_chunk({"role": "assistant", "content": "", "refusal": None})),
        (None, openai_chunk({"content": "Hello"})),
        (None, openai_chunk({"content": " there"})),
        (None, openai_chunk({"content": "!"})),
        (None, openai_chunk({}, finish_reason="stop")),
    ]
    if include_usage:
        events.append((None, openai_chunk(None, usage=OPENAI_STREAM_USAGE)))
    events.append((None, "[DONE]"))
    return events


OPENAI_TOOL_CALL_STREAM: list[tuple[str | None, dict[str, Any] | str]] = [
    (None, openai_chunk({"role": "assistant", "content": None})),
    (
        None,
        openai_chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_abc123",
                        "type": "function",
                        "function": {"name": "get_order", "arguments": ""},
                    }
                ]
            }
        ),
    ),
    (None, openai_chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"order'}}]})),
    (None, openai_chunk({"tool_calls": [{"index": 0, "function": {"arguments": '_id": "42"}'}}]})),
    (None, openai_chunk({}, finish_reason="tool_calls")),
    (None, "[DONE]"),
]


def openai_response_object(status: str = "completed") -> dict[str, Any]:
    return {
        "id": "resp_67ccd2bed1ec8190b14f964abc054267",
        "object": "response",
        "created_at": 1741476542,
        "status": status,
        "error": None,
        "incomplete_details": None,
        "instructions": "You are a support agent.",
        "max_output_tokens": None,
        "model": "gpt-4.1-2025-04-14",
        "output": [
            {
                "type": "message",
                "id": "msg_67ccd2bf17f0819081ff3bb2cf6508e6",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": "Your order has shipped.", "annotations": []}
                ],
            }
        ],
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "reasoning": {"effort": None, "summary": None},
        "store": True,
        "temperature": 1.0,
        "text": {"format": {"type": "text"}},
        "tool_choice": "auto",
        "tools": [],
        "top_p": 1.0,
        "truncation": "disabled",
        "usage": {
            "input_tokens": 36,
            "input_tokens_details": {"cached_tokens": 8},
            "output_tokens": 6,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 42,
        },
        "user": None,
        "metadata": {},
    }


def openai_responses_stream() -> list[tuple[str | None, dict[str, Any] | str]]:
    in_progress = {**openai_response_object(status="in_progress"), "output": [], "usage": None}
    return [
        (
            "response.created",
            {"type": "response.created", "sequence_number": 0, "response": in_progress},
        ),
        (
            "response.output_text.delta",
            {
                "type": "response.output_text.delta",
                "sequence_number": 1,
                "item_id": "msg_1",
                "output_index": 0,
                "content_index": 0,
                "delta": "Your order ",
                "logprobs": [],
            },
        ),
        (
            "response.output_text.delta",
            {
                "type": "response.output_text.delta",
                "sequence_number": 2,
                "item_id": "msg_1",
                "output_index": 0,
                "content_index": 0,
                "delta": "has shipped.",
                "logprobs": [],
            },
        ),
        (
            "response.completed",
            {
                "type": "response.completed",
                "sequence_number": 3,
                "response": openai_response_object(),
            },
        ),
    ]


OPENAI_RATE_LIMIT_ERROR = {
    "error": {
        "message": "Rate limit reached for gpt-4o-mini on tokens per min (TPM).",
        "type": "tokens",
        "param": None,
        "code": "rate_limit_exceeded",
    }
}


# ---------------------------------------------------------------------- #
# Anthropic payloads
# ---------------------------------------------------------------------- #

ANTHROPIC_MESSAGE = {
    "id": "msg_01XFDUDYJgAACzvnptvVoYEL",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-4-5-20251001",
    "content": [{"type": "text", "text": "Refunds take 5-7 business days."}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 12,
        "cache_creation_input_tokens": 3,
        "cache_read_input_tokens": 1500,
        "output_tokens": 9,
        "service_tier": "standard",
    },
}


# Real usage from a 2026-10-14 pilot call: every write landed in the 1-hour cache.
ANTHROPIC_CACHE_WRITE_USAGE = {
    "input_tokens": 19,
    "output_tokens": 12,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 6404,
    "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 6404},
}

ANTHROPIC_CACHE_WRITE_MESSAGE = {**ANTHROPIC_MESSAGE, "usage": ANTHROPIC_CACHE_WRITE_USAGE}


def anthropic_cache_write_stream() -> list[tuple[str | None, dict[str, Any] | str]]:
    """Text stream whose ``message_start`` carries the pilot's cache-write usage.

    ``message_delta`` repeats the cumulative prompt counts without the TTL split,
    as the API does; the accumulator must not sum them.
    """
    events = anthropic_text_stream()
    name, start = events[0]
    assert isinstance(start, dict)
    message = {**start["message"], "usage": {**ANTHROPIC_CACHE_WRITE_USAGE, "output_tokens": 1}}
    events[0] = (name, {**start, "message": message})
    name, delta = events[-2]
    assert isinstance(delta, dict)
    events[-2] = (
        name,
        {
            **delta,
            "usage": {
                "input_tokens": 19,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 6404,
                "output_tokens": 12,
            },
        },
    )
    return events


def anthropic_text_stream() -> list[tuple[str | None, dict[str, Any] | str]]:
    return [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_stream_01",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-haiku-4-5-20251001",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 25,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "output_tokens": 1,
                    },
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        ("ping", {"type": "ping"}),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hi"},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": ", how can I help?"},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 15},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]


def anthropic_tool_use_stream() -> list[tuple[str | None, dict[str, Any] | str]]:
    start = anthropic_text_stream()[0]
    return [
        start,
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_01T1x1fJ34qAmk2tNTrN7Up6",
                    "name": "get_order",
                    "input": {},
                },
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"order_id": '},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '"42"}'},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 40},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]


def anthropic_stream_with_overload_error() -> list[tuple[str | None, dict[str, Any] | str]]:
    return [
        *anthropic_text_stream()[:4],
        (
            "error",
            {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
        ),
    ]


ANTHROPIC_INVALID_REQUEST = {
    "type": "error",
    "error": {"type": "invalid_request_error", "message": "max_tokens: Field required"},
    "request_id": "req_011CSHoEeqs5C35K2UUqR7Fy",
}

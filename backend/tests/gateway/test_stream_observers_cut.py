"""Stream observers on a stream cut before its final usage, and on the new usage fields.

A stream that ends without the provider's final usage event has an unknown output count: the
summary keeps whatever input count the provider sent, says `output_tokens` is `None` and
`usage_complete` is False. Anthropic's `message_start` reports `output_tokens: 1` before any
token is generated; that placeholder is never the answer's count. The tier comes from the
provider's own field, and web searches are counted where the provider reports them.
"""

import json
from typing import Any

from app.gateway.responses_observer import OpenAiResponsesObserver
from app.gateway.sse import SseFrame, SseState, Usage, parse_frames
from app.gateway.stream_observers import AnthropicMessagesObserver, OpenAiChatObserver
from tests.gateway.support import anthropic_frame, chat_frames
from tests.gateway_support import fixture_text, sse_frames

# `message_start` of a Messages stream on the standard tier: the input is known, the output is
# the provider's placeholder of 1.
START_MESSAGE: dict[str, Any] = {
    "id": "msg_test_cut0002",
    "type": "message",
    "role": "assistant",
    "model": "claude-haiku-4-5-20251001",
    "content": [],
    "stop_reason": None,
    "stop_sequence": None,
    "usage": {
        "input_tokens": 100,
        "output_tokens": 1,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "service_tier": "standard",
    },
}
TEXT_FRAMES = [
    anthropic_frame("content_block_start", {"index": 0, "content_block": {"type": "text"}}),
    anthropic_frame(
        "content_block_delta", {"index": 0, "delta": {"type": "text_delta", "text": "Hel"}}
    ),
]
# The terminal event of a Responses stream that ran two web searches, one of which failed.
RESPONSES_DONE: dict[str, Any] = {
    "type": "response.completed",
    "sequence_number": 20,
    "response": {
        "id": "resp_test00000000000000000000000000000003",
        "object": "response",
        "created_at": 1760000120,
        "model": "gpt-4.1-mini-2025-04-14",
        "service_tier": "default",
        "status": "completed",
        "output": [
            {"id": "ws_1", "type": "web_search_call", "status": "completed"},
            {"id": "ws_2", "type": "web_search_call", "status": "failed"},
            {"id": "ws_3", "type": "web_search_call", "status": "completed"},
            {
                "id": "msg_test0000000000000000000000000000003",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "annotations": [], "text": "Found it."}],
            },
        ],
        "usage": {
            "input_tokens": 40,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 12,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 52,
        },
    },
}


def _frames(chunks: list[bytes]) -> list[SseFrame]:
    state = SseState()
    return [frame for chunk in chunks for frame in parse_frames(chunk, state)]


def _feed(observer: Any, chunks: list[bytes]) -> None:
    for frame in _frames(chunks):
        observer.on_frame(frame)


def test_anthropic_observer_without_message_delta_has_incomplete_usage() -> None:
    observer = AnthropicMessagesObserver()
    _feed(observer, [anthropic_frame("message_start", {"message": START_MESSAGE}), *TEXT_FRAMES])

    summary = observer.summary()

    assert summary.usage == Usage(100, None, 0, 0, None, service_tier="standard")
    assert summary.usage_complete is False
    assert summary.saw_output is True
    assert summary.output == {"role": "assistant", "content": [{"type": "text", "text": "Hel"}]}


def test_anthropic_observer_with_message_delta_is_complete() -> None:
    """`message_delta` carries the final output count and the cumulative search count."""
    observer = AnthropicMessagesObserver()
    delta = {
        "delta": {"stop_reason": "end_turn", "stop_sequence": None},
        "usage": {
            "input_tokens": 100,
            "output_tokens": 55,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "server_tool_use": {"web_search_requests": 2},
        },
    }
    _feed(
        observer,
        [
            anthropic_frame("message_start", {"message": START_MESSAGE}),
            *TEXT_FRAMES,
            anthropic_frame("message_delta", delta),
            anthropic_frame("message_stop", {}),
        ],
    )

    summary = observer.summary()

    assert summary.usage == Usage(
        100, 55, 0, 0, None, service_tier="standard", web_search_requests=2
    )
    assert summary.usage_complete is True
    assert summary.finish_reason == "end_turn"


def test_anthropic_message_delta_without_output_count_stays_incomplete() -> None:
    observer = AnthropicMessagesObserver()
    _feed(
        observer,
        [
            anthropic_frame("message_start", {"message": START_MESSAGE}),
            anthropic_frame("message_delta", {"delta": {"stop_reason": "end_turn"}, "usage": {}}),
        ],
    )

    summary = observer.summary()

    assert summary.usage is not None
    assert (summary.usage.output_tokens, summary.usage_complete) == (None, False)


def test_openai_chat_without_usage_chunk() -> None:
    """Deltas and a finish reason, but no usage chunk: the counts are unknown, not 0."""
    observer = OpenAiChatObserver()
    _feed(observer, chat_frames()[:11])  # up to the `finish_reason` chunk

    summary = observer.summary()

    assert summary.usage is None
    assert summary.usage_complete is False
    assert summary.saw_output is True
    assert summary.model == "gpt-4.1-mini-2025-04-14"
    assert summary.finish_reason == "stop"


def test_openai_chat_with_usage_chunk_is_complete_and_reads_tier() -> None:
    observer = OpenAiChatObserver()
    _feed(observer, chat_frames())

    summary = observer.summary()

    assert summary.usage == Usage(19, 9, 8, service_tier="standard")
    assert summary.usage_complete is True


def test_openai_chat_without_any_delta_saw_no_output() -> None:
    observer = OpenAiChatObserver()
    _feed(observer, chat_frames()[:1])  # the role chunk only

    summary = observer.summary()

    assert (summary.usage, summary.saw_output) == (None, False)


def test_responses_without_terminal_event() -> None:
    observer = OpenAiResponsesObserver()
    chunks = sse_frames(fixture_text("openai/responses_stream.sse"))
    _feed(observer, chunks[:-1])  # everything but `response.completed`

    summary = observer.summary()

    assert summary.usage is None
    assert summary.usage_complete is False
    assert summary.saw_output is True
    assert summary.model == "gpt-4.1-mini-2025-04-14"
    assert summary.finish_reason is None


def test_responses_counts_completed_web_search_calls_and_tier() -> None:
    observer = OpenAiResponsesObserver()
    frame = f"event: response.completed\ndata: {json.dumps(RESPONSES_DONE)}\n\n".encode()
    _feed(observer, [frame])

    summary = observer.summary()

    assert summary.usage == Usage(40, 12, 0, service_tier="standard", web_search_requests=2)
    assert summary.usage_complete is True
    assert summary.finish_reason == "completed"


def test_responses_stream_without_search_items_has_no_search_count() -> None:
    observer = OpenAiResponsesObserver()
    _feed(observer, sse_frames(fixture_text("openai/responses_stream.sse")))

    summary = observer.summary()

    assert summary.usage is not None
    assert summary.usage.web_search_requests is None
    assert summary.usage_complete is True


def test_responses_with_only_failed_searches_counts_zero() -> None:
    failed = {"id": "ws_2", "type": "web_search_call", "status": "failed"}
    done = {**RESPONSES_DONE, "response": {**RESPONSES_DONE["response"], "output": [failed]}}
    observer = OpenAiResponsesObserver()
    _feed(observer, [f"event: response.completed\ndata: {json.dumps(done)}\n\n".encode()])

    summary = observer.summary()

    assert summary.usage is not None
    assert summary.usage.web_search_requests == 0


def test_anthropic_message_delta_without_message_start_is_not_complete() -> None:
    """A final output count with no input count is no usage at all, so it is not complete."""
    observer = AnthropicMessagesObserver()
    delta = {"delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 12}}
    _feed(observer, [anthropic_frame("message_delta", delta)])

    summary = observer.summary()

    assert summary.usage is None
    assert summary.usage_complete is False

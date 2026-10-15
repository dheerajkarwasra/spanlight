"""Per-provider stream observers.

Observers read copies of the SSE frames to learn usage, model, finish reason and the
reassembled output. They never raise: a frame they cannot make sense of is skipped and the
summary simply carries less. Reassembled output is capped at ``MAX_OUTPUT_BYTES`` so a
runaway upstream cannot grow gateway memory; usage and finish reason keep updating after.
"""

import json
from typing import Any

from app.gateway.sse import (
    OutputBudget,
    SseFrame,
    StreamSummary,
    Usage,
    as_int,
    as_str,
    dict_at,
    json_object,
)
from app.gateway.usage import anthropic_usage, chat_usage, responses_usage

_NEW_SLOT_COST = 64  # charge for a new choice or tool call entry
_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)
# The cache-write split by TTL, nested under `usage.cache_creation`.
_CACHE_CREATION = "cache_creation"
_CACHE_CREATION_FIELDS = ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")
_STREAMED_FIELDS = {"text_delta": "text", "thinking_delta": "thinking"}
_RESPONSES_TERMINAL = frozenset({"response.completed", "response.incomplete", "response.failed"})


class OpenAiChatObserver:
    """Chat Completions chunks: deltas by choice index, usage from the final chunk."""

    def __init__(self) -> None:
        self._model: str | None = None
        self._response_id: str | None = None
        self._usage: Usage | None = None
        self._budget = OutputBudget()
        self._content: dict[int, list[str]] = {}
        self._finish: dict[int, str] = {}
        self._tool_calls: dict[int, dict[int, dict[str, Any]]] = {}
        self._choices: set[int] = set()

    def on_frame(self, frame: SseFrame) -> None:
        chunk = json_object(frame.data)
        if chunk is None:
            return
        self._model = as_str(chunk.get("model")) or self._model
        self._response_id = as_str(chunk.get("id")) or self._response_id
        self._usage = chat_usage(dict_at(chunk, "usage")) or self._usage
        choices = chunk.get("choices")
        for choice in choices if isinstance(choices, list) else []:
            if isinstance(choice, dict):
                self._on_choice(choice)

    def _on_choice(self, choice: dict[str, Any]) -> None:
        index = as_int(choice.get("index")) or 0
        if index not in self._choices:
            if not self._budget.spend(_NEW_SLOT_COST):
                return
            self._choices.add(index)
        delta = dict_at(choice, "delta")
        text = as_str(delta.get("content"))
        if text and self._budget.take(text):
            self._content.setdefault(index, []).append(text)
        calls = delta.get("tool_calls")
        for call in calls if isinstance(calls, list) else []:
            if isinstance(call, dict):
                self._on_tool_call(index, call)
        reason = as_str(choice.get("finish_reason"))
        if reason:
            self._finish[index] = reason

    def _on_tool_call(self, choice_index: int, call: dict[str, Any]) -> None:
        slots = self._tool_calls.setdefault(choice_index, {})  # index already admitted
        position = as_int(call.get("index")) or 0
        slot = slots.get(position)
        if slot is None:
            if not self._budget.spend(_NEW_SLOT_COST):
                return
            slot = slots[position] = {"id": None, "type": "function", "arguments": [], "name": []}
        slot["id"] = as_str(call.get("id")) or slot["id"]
        slot["type"] = as_str(call.get("type")) or slot["type"]
        function = dict_at(call, "function")
        for field in ("name", "arguments"):
            part = as_str(function.get(field))
            if part and self._budget.take(part):
                slot[field].append(part)

    def _message(self, index: int) -> dict[str, Any]:
        text = "".join(self._content.get(index, []))
        message: dict[str, Any] = {"role": "assistant", "content": text or None}
        calls = self._tool_calls.get(index)
        if calls:
            message["tool_calls"] = [
                {
                    "id": slot["id"],
                    "type": slot["type"],
                    "function": {
                        "name": "".join(slot["name"]),
                        "arguments": "".join(slot["arguments"]),
                    },
                }
                for _, slot in sorted(calls.items())
            ]
        return message

    def summary(self) -> StreamSummary:
        known = self._content.keys() | self._tool_calls.keys() | self._finish.keys()
        first = min(known, default=None)
        return StreamSummary(
            usage=self._usage,
            model=self._model,
            finish_reason=self._finish.get(first) if first is not None else None,
            output=self._message(first) if first is not None else None,
            response_id=self._response_id,
            output_truncated=self._budget.truncated,
        )


class OpenAiResponsesObserver:
    """Responses API events: the terminal event carries usage and the full output."""

    def __init__(self) -> None:
        self._model: str | None = None
        self._response_id: str | None = None
        self._usage: Usage | None = None
        self._status: str | None = None
        self._output: list[Any] | None = None

    def on_frame(self, frame: SseFrame) -> None:
        event = json_object(frame.data)
        if event is None:
            return
        response = dict_at(event, "response")
        self._model = as_str(response.get("model")) or self._model
        self._response_id = as_str(response.get("id")) or self._response_id
        if as_str(event.get("type")) not in _RESPONSES_TERMINAL:
            return
        self._status = as_str(response.get("status")) or self._status
        output = response.get("output")
        if isinstance(output, list):
            self._output = output  # one frame, already bounded by the frame size limit
        self._usage = responses_usage(dict_at(response, "usage")) or self._usage

    def summary(self) -> StreamSummary:
        return StreamSummary(
            usage=self._usage,
            model=self._model,
            finish_reason=self._status,
            output={"output": self._output} if self._output is not None else None,
            response_id=self._response_id,
        )


class AnthropicMessagesObserver:
    """Messages events: input usage from ``message_start``, the rest from ``message_delta``."""

    def __init__(self) -> None:
        self._model: str | None = None
        self._response_id: str | None = None
        self._stop_reason: str | None = None
        self._usage: dict[str, Any] = {}  # the Messages usage shape, merged across events
        self._budget = OutputBudget()
        self._blocks: dict[int, dict[str, Any]] = {}
        self._parts: dict[tuple[int, str], list[str]] = {}

    def on_frame(self, frame: SseFrame) -> None:
        event = json_object(frame.data)
        if event is None:
            return
        kind = as_str(event.get("type")) or frame.event
        index = as_int(event.get("index")) or 0
        if kind == "message_start":
            message = dict_at(event, "message")
            self._model = as_str(message.get("model")) or self._model
            self._response_id = as_str(message.get("id")) or self._response_id
            self._merge_usage(dict_at(message, "usage"))
        elif kind == "message_delta":
            delta = dict_at(event, "delta")
            self._stop_reason = as_str(delta.get("stop_reason")) or self._stop_reason
            self._merge_usage(dict_at(event, "usage"))
        elif kind == "content_block_start":
            block = dict_at(event, "content_block")
            if block and self._budget.spend(len(frame.data) + 1):  # the block is stored whole
                self._blocks[index] = dict(block)
        elif kind == "content_block_delta":
            self._on_delta(index, dict_at(event, "delta"))

    def _merge_usage(self, usage: dict[str, Any]) -> None:
        """Keep the latest value of each count: the events report totals, never increments.

        `message_start` carries the input counts and the cache-write split; `message_delta`
        repeats the cumulative counts (the split may be absent there), so a count is replaced,
        never added up, and the split survives an event that leaves it out.
        """
        for name in _USAGE_FIELDS:
            value = as_int(usage.get(name))
            if value is not None:
                self._usage[name] = value
        split = dict_at(usage, _CACHE_CREATION)
        for name in _CACHE_CREATION_FIELDS:
            value = as_int(split.get(name))
            if value is not None:
                self._usage.setdefault(_CACHE_CREATION, {})[name] = value

    def _on_delta(self, index: int, delta: dict[str, Any]) -> None:
        block = self._blocks.get(index)
        if block is None:
            return
        kind = as_str(delta.get("type"))
        if kind == "signature_delta":
            block["signature"] = as_str(delta.get("signature")) or block.get("signature")
            return
        if kind == "input_json_delta":
            field, part = "partial_json", as_str(delta.get("partial_json"))
        elif kind in _STREAMED_FIELDS:
            field = _STREAMED_FIELDS[kind]
            part = as_str(delta.get(field))
        else:
            return
        if part and self._budget.take(part):
            self._parts.setdefault((index, field), []).append(part)

    def _build_block(self, index: int) -> dict[str, Any]:
        block = dict(self._blocks[index])
        for field in ("text", "thinking"):
            parts = self._parts.get((index, field))
            if parts:
                block[field] = (as_str(block.get(field)) or "") + "".join(parts)
        partial = self._parts.get((index, "partial_json"))
        if partial is not None:
            raw = "".join(partial)
            try:
                block["input"] = json.loads(raw) if raw.strip() else {}
            except (ValueError, RecursionError):
                block["input"] = raw  # keep the raw string when the JSON is unfinished
        return block

    def summary(self) -> StreamSummary:
        blocks = [self._build_block(i) for i in sorted(self._blocks)]
        output = {"role": "assistant", "content": blocks} if blocks else None
        return StreamSummary(
            usage=anthropic_usage(self._usage),
            model=self._model,
            finish_reason=self._stop_reason,
            output=output,
            response_id=self._response_id,
            output_truncated=self._budget.truncated,
        )

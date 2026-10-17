"""Stream accumulators for OpenAI Chat Completions and the Responses API."""

from __future__ import annotations

from typing import Any

from .._span import Span
from ._common import Endpoint, as_int, get_field
from ._openai_response import first_str, record_chat_usage, usage_fields
from ._streams import StreamAccumulator, unreported_reason


class _ChoiceState:
    """Accumulated content of one streamed choice."""

    def __init__(self) -> None:
        self.role = "assistant"
        self.content: list[str] = []
        self.refusal: list[str] = []
        self.tool_calls: dict[int, dict[str, Any]] = {}
        self.finish_reason: str | None = None

    def apply(self, delta: Any) -> bool:
        """Merge one delta; return whether it carried generated output."""
        produced = False
        role = get_field(delta, "role")
        if isinstance(role, str):
            self.role = role
        content = get_field(delta, "content")
        if isinstance(content, str) and content:
            self.content.append(content)
            produced = True
        refusal = get_field(delta, "refusal")
        if isinstance(refusal, str) and refusal:
            self.refusal.append(refusal)
            produced = True
        for tool_call in get_field(delta, "tool_calls") or []:
            self._apply_tool_call(tool_call)
            produced = True
        return produced

    def _apply_tool_call(self, tool_call: Any) -> None:
        index = as_int(get_field(tool_call, "index")) or 0
        state = self.tool_calls.setdefault(
            index,
            {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
        )
        call_id = get_field(tool_call, "id")
        if isinstance(call_id, str):
            state["id"] = call_id
        name = get_field(tool_call, "function", "name")
        if isinstance(name, str):
            state["function"]["name"] += name
        arguments = get_field(tool_call, "function", "arguments")
        if isinstance(arguments, str):
            state["function"]["arguments"] += arguments

    def to_message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": self.role, "content": "".join(self.content) or None}
        if self.refusal:
            message["refusal"] = "".join(self.refusal)
        if self.tool_calls:
            message["tool_calls"] = [self.tool_calls[index] for index in sorted(self.tool_calls)]
        return message


class ChatStreamAccumulator(StreamAccumulator):
    """Rebuilds a chat completion from ``ChatCompletionChunk`` events."""

    def __init__(self, provider: str) -> None:
        self._provider = provider
        self._model: str | None = None
        self._response_id: str | None = None
        self._usage: Any = None
        self._service_tier: str | None = None
        self._choices: dict[int, _ChoiceState] = {}

    def on_event(self, event: Any, span: Span) -> None:
        model = get_field(event, "model")
        if isinstance(model, str) and model:
            self._model = model
        response_id = get_field(event, "id")
        if isinstance(response_id, str):
            self._response_id = response_id
        usage = get_field(event, "usage")
        if usage is not None:
            self._usage = usage
        service_tier = get_field(event, "service_tier")
        if isinstance(service_tier, str):
            self._service_tier = service_tier

        for choice in get_field(event, "choices") or []:
            index = as_int(get_field(choice, "index")) or 0
            state = self._choices.setdefault(index, _ChoiceState())
            if state.apply(get_field(choice, "delta")):
                span.mark_first_token()
            finish_reason = get_field(choice, "finish_reason")
            if isinstance(finish_reason, str):
                state.finish_reason = finish_reason

    def finish(
        self,
        span: Span,
        *,
        closed_early: bool = False,
        error: BaseException | None = None,
    ) -> None:
        span.set_model(self._model, provider=self._provider)
        record_chat_usage(span, self._usage)
        if self._service_tier is not None:
            span.set_usage(service_tier=self._service_tier)
        if self._usage is None:
            reason = unreported_reason(closed_early, error, otherwise="include_usage_not_set")
            span.set_attribute("usage.unreported_reason", reason)
        if self._response_id:
            span.set_attribute("response.id", self._response_id)

        states = [self._choices[index] for index in sorted(self._choices)]
        if not states:
            return
        finish_reasons = [state.finish_reason for state in states]
        span.set_attribute("response.finish_reasons", finish_reasons)
        span.set_finish_reason(first_str(finish_reasons[:1]))
        messages = [state.to_message() for state in states]
        span.set_output(messages[0] if len(messages) == 1 else messages)


class ResponsesStreamAccumulator(StreamAccumulator):
    """Tracks Responses API stream events; the final event carries the full response."""

    def __init__(self, endpoint: Endpoint) -> None:
        self._endpoint = endpoint
        self._final_response: Any = None
        self._last_usage: Any = None
        self._text: list[str] = []
        self._model: str | None = None
        self._error: str | None = None

    def on_event(self, event: Any, span: Span) -> None:
        event_type = get_field(event, "type")
        if not isinstance(event_type, str):
            return
        if event_type.endswith(".delta"):
            span.mark_first_token()
        if event_type == "response.output_text.delta":
            delta = get_field(event, "delta")
            if isinstance(delta, str):
                self._text.append(delta)

        response = get_field(event, "response")
        if response is not None:
            model = get_field(response, "model")
            if isinstance(model, str):
                self._model = model
            usage = get_field(response, "usage")
            if usage is not None:
                self._last_usage = usage
            if event_type in {"response.completed", "response.incomplete", "response.failed"}:
                self._final_response = response
        if event_type == "error":
            message = get_field(event, "message")
            self._error = message if isinstance(message, str) else "stream error"

    def finish(
        self,
        span: Span,
        *,
        closed_early: bool = False,
        error: BaseException | None = None,
    ) -> None:
        if self._final_response is not None:
            self._endpoint.record_response(span, self._final_response)
        else:
            self._finish_without_terminal_event(span, closed_early, error)
        if self._error is not None:
            span.record_error(self._error)

    def _finish_without_terminal_event(
        self, span: Span, closed_early: bool, error: BaseException | None
    ) -> None:
        """Record what arrived; the output count is unknown without the terminal event."""
        span.set_model(self._model, provider=self._endpoint.provider)
        span.set_output({"role": "assistant", "content": "".join(self._text) or None})
        if self._last_usage is not None:
            span.set_usage(**usage_fields(self._last_usage))
        span.clear_output_tokens()
        # An ``error`` event ends the stream as a failure even when the SDK raised nothing.
        failure = error or (RuntimeError(self._error) if self._error is not None else None)
        reason = unreported_reason(closed_early, failure, otherwise="no_final_usage")
        span.set_attribute("usage.unreported_reason", reason)

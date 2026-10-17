"""The Responses API stream observer.

Like the other observers (`stream_observers`), it reads copies of the SSE frames and never
raises: a frame it cannot make sense of is skipped and the summary simply carries less.
"""

from typing import Any

from app.gateway.sse import SseFrame, StreamSummary, Usage, as_str, dict_at, json_object
from app.gateway.usage import completed_web_searches, responses_usage

_RESPONSES_TERMINAL = frozenset({"response.completed", "response.incomplete", "response.failed"})


class OpenAiResponsesObserver:
    """Responses API events: the terminal event carries usage and the full output.

    Without a terminal event the usage is unknown: no earlier event carries a token count. The
    tier is the terminal response's `service_tier`, and the web searches are its `output` items
    of type `web_search_call` with `status == "completed"` (a failed search is not billed).
    """

    def __init__(self) -> None:
        self._model: str | None = None
        self._response_id: str | None = None
        self._usage: Usage | None = None
        self._status: str | None = None
        self._output: list[Any] | None = None
        self._saw_output = False

    def on_frame(self, frame: SseFrame) -> None:
        event = json_object(frame.data)
        if event is None:
            return
        response = dict_at(event, "response")
        self._model = as_str(response.get("model")) or self._model
        self._response_id = as_str(response.get("id")) or self._response_id
        kind = as_str(event.get("type")) or ""
        if kind.startswith("response.output"):
            self._saw_output = True  # an output item or a delta of one
        if kind not in _RESPONSES_TERMINAL:
            return
        self._status = as_str(response.get("status")) or self._status
        output = response.get("output")
        searches: int | None = None
        if isinstance(output, list):
            self._output = output  # one frame, already bounded by the frame size limit
            self._saw_output = self._saw_output or bool(output)
            searches = completed_web_searches(output)
        usage = responses_usage(
            dict_at(response, "usage"),
            service_tier=as_str(response.get("service_tier")),
            web_search_requests=searches,
        )
        self._usage = usage or self._usage

    def summary(self) -> StreamSummary:
        return StreamSummary(
            usage=self._usage,
            model=self._model,
            finish_reason=self._status,
            output={"output": self._output} if self._output is not None else None,
            response_id=self._response_id,
            usage_complete=self._usage is not None and self._usage.output_tokens is not None,
            saw_output=self._saw_output,
        )

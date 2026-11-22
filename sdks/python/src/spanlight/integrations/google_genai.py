"""Instrument the official Google Gen AI SDK's Generate Content calls."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Mapping
from functools import partial
from typing import TYPE_CHECKING, Any, TypeVar

from .._span import Span
from ._common import Endpoint, as_int, client_getter, get_field, patch_method
from ._google_request import request_body, wire_value
from ._streams import StreamAccumulator, unreported_reason
from ._wrappers import wrap_async_method, wrap_sync_method

if TYPE_CHECKING:
    from .._client import Spanlight

logger = logging.getLogger("spanlight")
ClientT = TypeVar("ClientT")
_SURFACE = "generate_content"


def wrap_google_genai(
    client: ClientT, *, spanlight: Spanlight | None = None, provider: str = "google"
) -> ClientT:
    """Trace Generate Content on a Google client or its asynchronous ``aio`` client.

    The original client is returned. ``spanlight`` defaults to the global client and
    ``provider`` controls attribution on the emitted spans.
    """
    try:
        get_client = client_getter(spanlight)
        for target in (client, getattr(client, "aio", None)):
            models = getattr(target, "models", None)
            for name, streaming in (("generate_content", False), ("generate_content_stream", True)):
                endpoint = _GenerateContent(provider, streaming=streaming)
                wrap = (
                    wrap_async_method
                    if inspect.iscoroutinefunction(getattr(models, name, None))
                    else wrap_sync_method
                )
                patch_method(models, name, partial(wrap, endpoint=endpoint, get_client=get_client))
    except Exception:
        logger.warning("Spanlight: could not instrument Google Gen AI client", exc_info=True)
    return client


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _count(value: Any) -> int | None:
    count = as_int(value)
    return count if count is not None and count >= 0 else None


def _record_usage(span: Span, usage: Any, *, finished: bool, blocked: bool) -> None:
    prompt = _count(get_field(usage, "prompt_token_count"))
    cached = _count(get_field(usage, "cached_content_token_count"))
    answer = _count(get_field(usage, "candidates_token_count"))
    thoughts = _count(get_field(usage, "thoughts_token_count"))
    output = (
        (answer or 0) + (thoughts or 0) if finished and (answer is not None or blocked) else None
    )
    span.set_usage(
        input_tokens=prompt,
        output_tokens=output,
        cached_tokens=cached
        if cached is not None and prompt is not None and cached <= prompt
        else None,
        service_tier=_text(get_field(usage, "service_tier")),
    )
    if finished and thoughts is not None:
        span.set_attribute("usage.reasoning_tokens", thoughts)


def _block_reason(response: Any) -> str | None:
    reason = _text(get_field(response, "prompt_feedback", "block_reason"))
    return None if reason == "BLOCK_REASON_UNSPECIFIED" else reason


def _response_id(span: Span, response_id: str | None) -> None:
    if response_id:
        span.set_attribute("response.id", response_id)


class _GenerateContent(Endpoint):
    operation = _SURFACE

    def __init__(self, provider: str, *, streaming: bool) -> None:
        super().__init__(provider)
        self._streaming = streaming

    def request_attributes(self, request: Mapping[str, Any]) -> dict[str, Any]:
        attrs: dict[str, Any] = {"spanlight.gateway.surface": _SURFACE}
        if self._streaming:
            attrs["request.stream"] = True
        return attrs

    def request_body(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return request_body(request, streaming=self._streaming)

    def is_stream(self, result: Any) -> bool:
        return self._streaming

    def new_accumulator(self) -> StreamAccumulator:
        return _GeminiStream()

    def record_response(self, span: Span, response: Any) -> None:
        span.set_model(_text(get_field(response, "model_version")))
        _response_id(span, _text(get_field(response, "response_id")))
        first = next(iter(get_field(response, "candidates") or []), None)
        block = _block_reason(response)
        span.set_finish_reason(_text(get_field(first, "finish_reason")) or block)
        span.set_output(wire_value(get_field(first, "content")))
        _record_usage(
            span, get_field(response, "usage_metadata"), finished=True, blocked=block is not None
        )


class _GeminiStream(StreamAccumulator):
    def __init__(self) -> None:
        self.model: str | None = None
        self.response_id: str | None = None
        self.reason: str | None = None
        self.blocked = False
        self.usage: Any = None
        self.final_usage: Any = None
        self.parts: list[Any] = []
        self.finished_candidates: set[int] = set()

    def on_event(self, event: Any, span: Span) -> None:
        self.model = _text(get_field(event, "model_version")) or self.model
        self.response_id = _text(get_field(event, "response_id")) or self.response_id
        if block := _block_reason(event):
            self.reason = block
            self.blocked = True
        count = _count(get_field(span.input, "generationConfig", "candidateCount")) or 1
        for position, candidate in enumerate(get_field(event, "candidates") or []):
            index = _count(get_field(candidate, "index"))
            if index is None:
                index = position
            reason = _text(get_field(candidate, "finish_reason"))
            if index < count and reason and reason != "FINISH_REASON_UNSPECIFIED":
                self.finished_candidates.add(index)
                if index == 0:
                    self.reason = reason
            parts = get_field(candidate, "content", "parts")
            if index == 0 and parts:
                span.mark_first_token()
                self.parts.extend(parts)
        usage = get_field(event, "usage_metadata")
        if usage is not None:
            self.usage = usage
            if self.blocked or len(self.finished_candidates) >= count:
                self.final_usage = usage

    def finish(
        self, span: Span, *, closed_early: bool = False, error: BaseException | None = None
    ) -> None:
        span.set_model(self.model)
        _response_id(span, self.response_id)
        span.set_finish_reason(self.reason)
        if self.parts:
            span.set_output({"role": "model", "parts": wire_value(self.parts)})
        final = error is None and self.final_usage is not None
        _record_usage(
            span, self.final_usage if final else self.usage, finished=final, blocked=self.blocked
        )
        if span.output_tokens is None:
            span.set_attribute(
                "usage.unreported_reason",
                unreported_reason(closed_early, error, otherwise="missing_final_usage"),
            )

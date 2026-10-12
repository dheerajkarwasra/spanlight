"""Instrumentation for the official ``anthropic`` Python SDK.

:func:`wrap_anthropic` patches ``messages.create`` (including
``stream=True``) and the ``messages.stream(...)`` helper on one client
instance, sync or async.

Token accounting: Anthropic reports ``input_tokens`` *excluding* prompt-cache
reads and writes. To keep one meaning across providers, spans record
``input_tokens`` as the total prompt size (uncached + cache reads + cache
writes) and ``cached_tokens`` as the cache reads. The raw cache-write count is
kept in the ``usage.cache_creation_input_tokens`` attribute.
"""

from __future__ import annotations

import functools
import json
import logging
from collections.abc import Callable
from types import TracebackType
from typing import TYPE_CHECKING, Any, TypeVar

from .._span import Span
from ._common import (
    WRAPPED_MARKER,
    AsyncStreamProxy,
    ClientGetter,
    Endpoint,
    StreamAccumulator,
    SyncStreamProxy,
    as_int,
    client_getter,
    finish_with_error,
    get_field,
    patch_method,
    pop_prompt,
    start_llm_span,
    wrap_async_method,
    wrap_sync_method,
)

if TYPE_CHECKING:
    from .._client import Spanlight

logger = logging.getLogger("spanlight")

ClientT = TypeVar("ClientT")

PROVIDER = "anthropic"


def wrap_anthropic(client: ClientT, *, spanlight: Spanlight | None = None) -> ClientT:
    """Instrument an ``Anthropic``/``AsyncAnthropic`` client in place.

    ``messages.create`` (plain and ``stream=True``) and ``messages.stream``
    become ``llm`` spans with model, token usage, time-to-first-token for
    streams, sanitized request parameters and the assistant message. Errors
    are recorded and re-raised unchanged. Wrapping twice is harmless.

    Args:
        client: The Anthropic client to instrument.
        spanlight: Spanlight client to report to; defaults to the global client at call time.

    Returns:
        The same client object, for convenient chaining.
    """
    try:
        _instrument(client, spanlight)
    except Exception:
        logger.warning("Spanlight: could not instrument Anthropic client", exc_info=True)
    return client


def _instrument(client: Any, spanlight: Spanlight | None) -> None:
    from anthropic import AsyncStream, Stream
    from anthropic.resources.messages import AsyncMessages

    get_client = client_getter(spanlight)
    endpoint = MessagesEndpoint(PROVIDER, (Stream, AsyncStream))
    messages = client.messages
    is_async = isinstance(messages, AsyncMessages)

    wrap = wrap_async_method if is_async else wrap_sync_method
    patch_method(messages, "create", lambda original: wrap(original, endpoint, get_client))

    manager_cls = _AsyncStreamManager if is_async else _SyncStreamManager

    def wrap_stream_helper(original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        def stream(*args: Any, **kwargs: Any) -> Any:
            prompt = pop_prompt(kwargs)
            manager = original(*args, **kwargs)
            return manager_cls(manager, endpoint, get_client, kwargs, prompt)

        setattr(stream, WRAPPED_MARKER, True)
        return stream

    patch_method(messages, "stream", wrap_stream_helper)


class MessagesEndpoint(Endpoint):
    """``messages.create``."""

    operation = "chat"

    def __init__(self, provider: str, stream_types: tuple[type, ...]) -> None:
        super().__init__(provider)
        self._stream_types = stream_types

    def is_stream(self, result: Any) -> bool:
        return isinstance(result, self._stream_types)

    def new_accumulator(self) -> StreamAccumulator:
        return _MessagesStreamAccumulator()

    def record_response(self, span: Span, response: Any) -> None:
        span.set_model(get_field(response, "model"), provider=PROVIDER)
        _record_usage(span, get_field(response, "usage"))
        _record_message_metadata(
            span, get_field(response, "id"), get_field(response, "stop_reason")
        )
        span.set_output({"role": "assistant", "content": get_field(response, "content") or []})


def _record_usage(span: Span, usage: Any) -> None:
    if usage is None:
        return
    uncached = as_int(get_field(usage, "input_tokens"))
    cache_read = as_int(get_field(usage, "cache_read_input_tokens"))
    cache_write = as_int(get_field(usage, "cache_creation_input_tokens"))
    total_input = None
    if uncached is not None:
        total_input = uncached + (cache_read or 0) + (cache_write or 0)
    span.set_usage(
        input_tokens=total_input,
        output_tokens=as_int(get_field(usage, "output_tokens")),
        cached_tokens=cache_read,
    )
    if cache_write:
        span.set_attribute("usage.cache_creation_input_tokens", cache_write)


def _record_message_metadata(span: Span, message_id: Any, stop_reason: Any) -> None:
    if isinstance(message_id, str):
        span.set_attribute("response.id", message_id)
    if isinstance(stop_reason, str):
        span.set_attribute("response.stop_reason", stop_reason)
        span.set_finish_reason(stop_reason)


class _MessagesStreamAccumulator(StreamAccumulator):
    """Rebuilds a ``Message`` from raw ``RawMessageStreamEvent`` events."""

    def __init__(self) -> None:
        self._model: str | None = None
        self._message_id: str | None = None
        self._stop_reason: str | None = None
        self._usage: dict[str, Any] = {}
        self._blocks: dict[int, dict[str, Any]] = {}
        self._partial_json: dict[int, list[str]] = {}

    def on_event(self, event: Any, span: Span) -> None:
        event_type = get_field(event, "type")
        if event_type == "message_start":
            self._on_message_start(get_field(event, "message"))
        elif event_type == "content_block_start":
            self._on_block_start(event)
        elif event_type == "content_block_delta":
            span.mark_first_token()
            self._on_block_delta(event)
        elif event_type == "message_delta":
            stop_reason = get_field(event, "delta", "stop_reason")
            if isinstance(stop_reason, str):
                self._stop_reason = stop_reason
            self._merge_usage(get_field(event, "usage"))

    def _on_message_start(self, message: Any) -> None:
        model = get_field(message, "model")
        if isinstance(model, str):
            self._model = model
        message_id = get_field(message, "id")
        if isinstance(message_id, str):
            self._message_id = message_id
        self._merge_usage(get_field(message, "usage"))

    def _merge_usage(self, usage: Any) -> None:
        """Merge usage counters; later events carry cumulative values."""
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            value = as_int(get_field(usage, key))
            if value is not None:
                self._usage[key] = value

    def _on_block_start(self, event: Any) -> None:
        index = as_int(get_field(event, "index")) or 0
        block = get_field(event, "content_block")
        dumped = getattr(block, "model_dump", None)
        if callable(dumped):
            self._blocks[index] = dumped(mode="json", exclude_none=True)
        elif isinstance(block, dict):
            self._blocks[index] = dict(block)
        else:
            self._blocks[index] = {"type": get_field(block, "type")}

    def _on_block_delta(self, event: Any) -> None:
        index = as_int(get_field(event, "index")) or 0
        block = self._blocks.setdefault(index, {"type": "text", "text": ""})
        delta = get_field(event, "delta")
        delta_type = get_field(delta, "type")
        if delta_type == "text_delta":
            block["text"] = block.get("text", "") + (get_field(delta, "text") or "")
        elif delta_type == "thinking_delta":
            block["thinking"] = block.get("thinking", "") + (get_field(delta, "thinking") or "")
        elif delta_type == "input_json_delta":
            self._partial_json.setdefault(index, []).append(get_field(delta, "partial_json") or "")

    def finish(self, span: Span) -> None:
        span.set_model(self._model, provider=PROVIDER)
        if self._usage:
            _record_usage(span, self._usage)
        _record_message_metadata(span, self._message_id, self._stop_reason)
        for index, fragments in self._partial_json.items():
            self._blocks[index]["input"] = _parse_json("".join(fragments))
        content = [self._blocks[index] for index in sorted(self._blocks)]
        span.set_output({"role": "assistant", "content": content})


def _parse_json(raw: str) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return raw


class _StreamManagerBase:
    """Shared state for the ``messages.stream(...)`` manager wrappers.

    The Anthropic helper builds a ``MessageStream`` around a raw event stream
    stored in its ``_raw_stream`` attribute. We swap that raw stream for our
    observing proxy, so the helper's own event processing (``text_stream``,
    ``get_final_message()``...) keeps working untouched while every event
    passes through the accumulator. If a future SDK renames the attribute,
    the span still records request data and timing, just not the response.
    """

    def __init__(
        self,
        manager: Any,
        endpoint: MessagesEndpoint,
        get_client: ClientGetter,
        request: dict[str, Any],
        prompt: Any = None,
    ) -> None:
        self._manager = manager
        self._endpoint = endpoint
        self._get_client = get_client
        self._request = {**request, "stream": True}
        self._prompt = prompt
        self._span: Span | None = None
        self._proxy: SyncStreamProxy | AsyncStreamProxy | None = None

    def __getattr__(self, name: str) -> Any:
        if name == "_manager":
            raise AttributeError(name)
        return getattr(self._manager, name)

    def _attach(self, message_stream: Any, *, is_async: bool) -> None:
        span = self._span
        if span is None:
            return
        try:
            raw_stream = getattr(message_stream, "_raw_stream", None)
            if raw_stream is None:
                return
            proxy_cls = AsyncStreamProxy if is_async else SyncStreamProxy
            self._proxy = proxy_cls(raw_stream, self._endpoint.new_accumulator(), span)
            message_stream._raw_stream = self._proxy
        except Exception:
            logger.debug("Could not attach to Anthropic message stream", exc_info=True)

    def _finish(self, error: BaseException | None) -> None:
        span = self._span
        if span is None:
            return
        if self._proxy is not None:
            self._proxy.finalize(error)
        elif error is not None:
            finish_with_error(span, error)
        else:
            span.end()


class _SyncStreamManager(_StreamManagerBase):
    """Traced replacement for ``anthropic.lib.streaming.MessageStreamManager``."""

    def __enter__(self) -> Any:
        self._span = start_llm_span(self._get_client, self._endpoint, self._request, self._prompt)
        try:
            message_stream = self._manager.__enter__()
        except BaseException as exc:
            self._finish(exc)
            raise
        self._attach(message_stream, is_async=False)
        return message_stream

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            self._manager.__exit__(exc_type, exc, traceback)
        finally:
            self._finish(None)


class _AsyncStreamManager(_StreamManagerBase):
    """Traced replacement for ``anthropic.lib.streaming.AsyncMessageStreamManager``."""

    async def __aenter__(self) -> Any:
        self._span = start_llm_span(self._get_client, self._endpoint, self._request, self._prompt)
        try:
            message_stream = await self._manager.__aenter__()
        except BaseException as exc:
            self._finish(exc)
            raise
        self._attach(message_stream, is_async=True)
        return message_stream

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            await self._manager.__aexit__(exc_type, exc, traceback)
        finally:
            self._finish(None)


__all__ = ["wrap_anthropic"]

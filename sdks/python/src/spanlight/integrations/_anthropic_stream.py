"""Streaming Anthropic responses: the event accumulator and the ``messages.stream`` helper.

:class:`MessagesStreamAccumulator` rebuilds a ``Message`` from raw stream
events for ``messages.create(stream=True)``; the stream managers wrap the
``messages.stream(...)`` helper so its events pass through the same
accumulator.
"""

from __future__ import annotations

import json
import logging
from types import TracebackType
from typing import Any

from .._span import Span
from ._anthropic_message import PROVIDER, record_message_metadata, record_usage
from ._common import ClientGetter, Endpoint, as_int, finish_with_error, get_field, start_llm_span
from ._streams import AsyncStreamProxy, StreamAccumulator, SyncStreamProxy, unreported_reason

logger = logging.getLogger("spanlight")


class MessagesStreamAccumulator(StreamAccumulator):
    """Rebuilds a ``Message`` from raw ``RawMessageStreamEvent`` events."""

    def __init__(self) -> None:
        self._model: str | None = None
        self._message_id: str | None = None
        self._stop_reason: str | None = None
        self._usage: dict[str, Any] = {}
        # Set by the ``message_delta`` that carries the final output count. Without
        # it the stream was cut and the output count is unknown.
        self._final_usage_seen = False
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
            usage = get_field(event, "usage")
            if as_int(get_field(usage, "output_tokens")) is not None:
                self._final_usage_seen = True
            self._merge_usage(usage)

    def _on_message_start(self, message: Any) -> None:
        model = get_field(message, "model")
        if isinstance(model, str):
            self._model = model
        message_id = get_field(message, "id")
        if isinstance(message_id, str):
            self._message_id = message_id
        self._merge_usage(get_field(message, "usage"))

    def _merge_usage(self, usage: Any) -> None:
        """Merge usage counters; later events carry cumulative values, never summed.

        The TTL split (``cache_creation``) usually appears only in
        ``message_start``; a later event without it leaves the split in place.
        """
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            value = as_int(get_field(usage, key))
            if value is not None:
                self._usage[key] = value
        cache_write_1h = as_int(get_field(usage, "cache_creation", "ephemeral_1h_input_tokens"))
        if cache_write_1h is not None:
            self._usage["cache_creation"] = {"ephemeral_1h_input_tokens": cache_write_1h}
        service_tier = get_field(usage, "service_tier")
        if isinstance(service_tier, str):
            self._usage["service_tier"] = service_tier
        # The web search count is cumulative too; usually only ``message_delta`` has it.
        web_searches = as_int(get_field(usage, "server_tool_use", "web_search_requests"))
        if web_searches is not None:
            self._usage["server_tool_use"] = {"web_search_requests": web_searches}

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

    def finish(
        self,
        span: Span,
        *,
        closed_early: bool = False,
        error: BaseException | None = None,
    ) -> None:
        span.set_model(self._model, provider=PROVIDER)
        if self._usage:
            record_usage(span, self._usage)
        if not self._final_usage_seen:
            # The prompt counts from ``message_start`` stand; its ``output_tokens``
            # is a placeholder, so the output count is exported as unknown.
            span.clear_output_tokens()
            reason = unreported_reason(closed_early, error, otherwise="no_final_usage")
            span.set_attribute("usage.unreported_reason", reason)
        record_message_metadata(span, self._message_id, self._stop_reason)
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
        endpoint: Endpoint,
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
            # Runs from ``__exit__``: a stream not yet exhausted was closed early.
            # Exhausted streams finalized themselves, so this is then a no-op.
            self._proxy.finalize(error, closed_early=True)
        elif error is not None:
            finish_with_error(span, error)
        else:
            span.end()


class SyncStreamManager(_StreamManagerBase):
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


class AsyncStreamManager(_StreamManagerBase):
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

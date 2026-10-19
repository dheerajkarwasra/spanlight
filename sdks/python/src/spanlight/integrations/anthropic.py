"""Instrumentation for the official ``anthropic`` Python SDK.

:func:`wrap_anthropic` patches ``messages.create`` (including
``stream=True``), ``messages.parse``, the ``messages.stream(...)`` helper and
``messages.batches.results`` on one client instance, sync or async, on its beta
surface (``client.beta.messages``) and on the clients its ``copy()`` and
``with_options()`` return.

Token accounting: Anthropic reports ``input_tokens`` *excluding* prompt-cache
reads and writes. To keep one meaning across providers, spans record
``input_tokens`` as the total prompt size (uncached + cache reads + cache
writes), ``cached_tokens`` as the cache reads, ``cache_write_tokens`` as the
cache writes (``cache_creation_input_tokens``) and ``cache_write_1h_tokens``
as the writes made with the 1-hour TTL
(``cache_creation.ephemeral_1h_input_tokens``), which the server prices apart.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeVar

from .._span import Span
from ._anthropic_message import PROVIDER, record_message_metadata, record_usage
from ._anthropic_stream import AsyncStreamManager, MessagesStreamAccumulator, SyncStreamManager
from ._common import (
    WRAPPED_MARKER,
    ClientGetter,
    Endpoint,
    client_getter,
    get_field,
    patch_copies,
    patch_method,
    pop_prompt,
)
from ._streams import StreamAccumulator
from ._wrappers import wrap_async_method, wrap_sync_method

if TYPE_CHECKING:
    from .._client import Spanlight

logger = logging.getLogger("spanlight")

ClientT = TypeVar("ClientT")


def wrap_anthropic(client: ClientT, *, spanlight: Spanlight | None = None) -> ClientT:
    """Instrument an ``Anthropic``/``AsyncAnthropic`` client in place.

    ``messages.create`` (plain and ``stream=True``) and ``messages.stream``
    become ``llm`` spans with model, token usage, time-to-first-token for
    streams, sanitized request parameters and the assistant message. Errors
    are recorded and re-raised unchanged. Each result read through
    ``messages.batches.results`` becomes an ``llm`` span at the batch rate in
    a trace of its own, with ids fixed by the batch, so reading a batch twice
    records it once. ``messages.parse`` and the same methods of
    ``client.beta.messages`` are traced too, and so are clients made with
    ``client.with_options(...)`` or ``client.copy(...)``. Wrapping twice is
    harmless.

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
    _instrument_messages(client.messages, endpoint, get_client, AsyncMessages)
    patch_copies(client, lambda copy: wrap_anthropic(copy, spanlight=spanlight))
    # Fast mode, compaction and the tool runner exist only on the beta surface.
    from anthropic.resources.beta.messages import AsyncMessages as AsyncBetaMessages

    _instrument_messages(client.beta.messages, endpoint, get_client, AsyncBetaMessages)


def _instrument_messages(
    messages: Any, endpoint: MessagesEndpoint, get_client: ClientGetter, async_cls: type
) -> None:
    from ._anthropic_batch import patch_batch_results

    is_async = isinstance(messages, async_cls)
    wrap = wrap_async_method if is_async else wrap_sync_method
    # `parse` is a separate request, not a call to `create`, so it needs its own span.
    for name in ("create", "parse"):
        patch_method(messages, name, lambda original: wrap(original, endpoint, get_client))

    manager_cls = AsyncStreamManager if is_async else SyncStreamManager

    def wrap_stream_helper(original: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(original)
        def stream(*args: Any, **kwargs: Any) -> Any:
            prompt = pop_prompt(kwargs)
            manager = original(*args, **kwargs)
            return manager_cls(manager, endpoint, get_client, kwargs, prompt)

        setattr(stream, WRAPPED_MARKER, True)
        return stream

    patch_method(messages, "stream", wrap_stream_helper)
    patch_batch_results(messages, get_client, is_async=is_async)


class MessagesEndpoint(Endpoint):
    """``messages.create``."""

    operation = "chat"

    def __init__(self, provider: str, stream_types: tuple[type, ...]) -> None:
        super().__init__(provider)
        self._stream_types = stream_types

    def is_stream(self, result: Any) -> bool:
        return isinstance(result, self._stream_types)

    def new_accumulator(self) -> StreamAccumulator:
        return MessagesStreamAccumulator()

    def record_response(self, span: Span, response: Any) -> None:
        span.set_model(get_field(response, "model"), provider=PROVIDER)
        record_usage(span, get_field(response, "usage"))
        record_message_metadata(span, get_field(response, "id"), get_field(response, "stop_reason"))
        span.set_output({"role": "assistant", "content": get_field(response, "content") or []})


__all__ = ["wrap_anthropic"]

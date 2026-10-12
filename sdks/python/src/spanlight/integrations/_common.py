"""Provider-agnostic plumbing shared by the OpenAI and Anthropic wrappers.

Each instrumented endpoint is described by an :class:`Endpoint`, which knows
how to name the span and how to read a provider response. The generic
wrappers in this module take care of the rest: starting the span, recording
errors without altering them, and wrapping streaming responses in a proxy
that observes events as the caller consumes them.
"""

from __future__ import annotations

import functools
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from types import TracebackType
from typing import TYPE_CHECKING, Any

from .._request_hash import request_hash
from .._serialize import to_jsonable
from .._span import Span

if TYPE_CHECKING:
    from .._client import Spanlight

logger = logging.getLogger("spanlight")

ClientGetter = Callable[[], "Spanlight"]

WRAPPED_MARKER = "__spanlight_wrapped__"

# Request options that configure transport/auth rather than describe the
# prompt. They are never recorded: headers in particular may carry secrets.
_EXCLUDED_REQUEST_KEYS = frozenset(
    {"api_key", "extra_headers", "extra_query", "extra_body", "timeout", "http_client"}
)
_SENTINEL_TYPE_NAMES = frozenset({"NotGiven", "Omit"})
_RAW_RESPONSE_HEADER = "x-stainless-raw-response"

# Keyword a wrapped call accepts to link its span to a compiled prompt. It is removed from the
# call before the provider SDK sees it.
PROMPT_KWARG = "spanlight_prompt"


class StreamAccumulator(ABC):
    """Collects streamed events and writes the final result onto a span."""

    @abstractmethod
    def on_event(self, event: Any, span: Span) -> None:
        """Observe one streamed event (e.g. to mark time-to-first-token)."""

    @abstractmethod
    def finish(self, span: Span) -> None:
        """Record the accumulated model, usage and output on ``span``."""


class Endpoint(ABC):
    """Describes how to trace one provider API method."""

    provider: str
    operation: str

    def __init__(self, provider: str) -> None:
        self.provider = provider

    def span_name(self, request: Mapping[str, Any]) -> str:
        """Return the span name, e.g. ``"chat gpt-4o-mini"``."""
        model = request.get("model")
        return f"{self.operation} {model}" if isinstance(model, str) else self.operation

    @abstractmethod
    def record_response(self, span: Span, response: Any) -> None:
        """Copy model, usage and output from a non-streaming response onto ``span``."""

    @abstractmethod
    def new_accumulator(self) -> StreamAccumulator:
        """Return a fresh accumulator for one streaming response."""

    @abstractmethod
    def is_stream(self, result: Any) -> bool:
        """Return whether ``result`` is a streaming response object."""


def client_getter(spanlight: Spanlight | None) -> ClientGetter:
    """Return a callable resolving the client to report to.

    An explicit ``spanlight`` is bound immediately; otherwise the global client
    is looked up on every call so ``init()`` may run after wrapping.
    """
    if spanlight is None:
        from .._globals import get_client

        return get_client

    def bound() -> Spanlight:
        return spanlight

    return bound


def sanitize_request(kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """Return the request parameters worth recording as span input.

    Drops transport/auth options and the SDKs' "not given" sentinels, then
    converts everything to JSON-compatible values.
    """
    cleaned: dict[str, Any] = {}
    for key, value in kwargs.items():
        if key in _EXCLUDED_REQUEST_KEYS:
            continue
        if type(value).__name__ in _SENTINEL_TYPE_NAMES:
            continue
        cleaned[key] = to_jsonable(value)
    return cleaned


def is_raw_response_call(kwargs: Mapping[str, Any]) -> bool:
    """Whether the call comes from ``.with_raw_response`` / ``.with_streaming_response``.

    Those helpers call ``create`` with a marker header and expect the raw
    HTTP response back, so they are passed through untraced.
    """
    headers = kwargs.get("extra_headers")
    if not isinstance(headers, Mapping):
        return False
    return any(str(key).lower() == _RAW_RESPONSE_HEADER for key in headers)


def get_field(obj: Any, *path: str) -> Any:
    """Read a nested attribute or mapping key, returning ``None`` if absent."""
    current = obj
    for name in path:
        if current is None:
            return None
        if isinstance(current, Mapping):
            current = current.get(name)
        else:
            current = getattr(current, name, None)
    return current


def as_int(value: Any) -> int | None:
    """Return ``value`` if it is an ``int`` (but not a ``bool``), else ``None``."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def record_provider_error(span: Span, error: BaseException) -> None:
    """Mark ``span`` failed with details from a provider SDK exception."""
    span.record_error(error)
    status_code = as_int(getattr(error, "status_code", None))
    if status_code is not None:
        span.set_attribute("http.status_code", status_code)
    request_id = getattr(error, "request_id", None)
    if isinstance(request_id, str):
        span.set_attribute("provider.request_id", request_id)


def pop_prompt(kwargs: dict[str, Any]) -> Any:
    """Remove and return the ``spanlight_prompt=`` keyword, so it never reaches the provider."""
    return kwargs.pop(PROMPT_KWARG, None)


def link_prompt(span: Span, prompt: Any) -> None:
    """Link ``span`` to the version of a :class:`~spanlight.CompiledPrompt`.

    A fallback prompt (no version) clears any link inherited from an enclosing
    ``with compiled:`` block, because the call did not use that version.
    """
    version_id = getattr(prompt, "prompt_version_id", None)
    span.set_prompt_version(version_id if isinstance(version_id, str) else None)


def start_llm_span(
    get_client: ClientGetter,
    endpoint: Endpoint,
    kwargs: Mapping[str, Any],
    prompt: Any = None,
) -> Span | None:
    """Start an ``llm`` span for a provider call, or ``None`` to run untraced.

    ``prompt`` is the call's ``spanlight_prompt=`` value, already removed from ``kwargs``.
    """
    try:
        if is_raw_response_call(kwargs):
            return None
        client = get_client()
        if not client.enabled:
            return None
        span = client.start_span(endpoint.span_name(kwargs), kind="llm")
        model = kwargs.get("model")
        span.set_model(model if isinstance(model, str) else None, provider=endpoint.provider)
        request = sanitize_request(kwargs)
        span.set_request_hash(_request_hash_or_none(model, request))
        span.set_input(request)
        if kwargs.get("stream") is True:
            span.set_attribute("request.stream", True)
        if prompt is not None:
            link_prompt(span, prompt)
        return span
    except Exception:
        logger.debug("Failed to start LLM span", exc_info=True)
        return None


def _request_hash_or_none(model: Any, request: Any) -> str | None:
    """Return the request hash, or ``None`` when it cannot be computed.

    An optional fingerprint must never cost the call its span.
    """
    try:
        return request_hash(model if isinstance(model, str) else None, request)
    except Exception:
        logger.debug("Failed to hash the request", exc_info=True)
        return None


def finish_with_response(span: Span, endpoint: Endpoint, result: Any) -> Any:
    """Record a successful provider result; wrap it if it is a stream."""
    try:
        if endpoint.is_stream(result):
            return _wrap_stream(result, endpoint.new_accumulator(), span)
        endpoint.record_response(span, result)
    except Exception:
        logger.debug("Failed to record provider response", exc_info=True)
    span.end()
    return result


def _wrap_stream(result: Any, accumulator: StreamAccumulator, span: Span) -> Any:
    if hasattr(result, "__anext__"):
        return AsyncStreamProxy(result, accumulator, span)
    return SyncStreamProxy(result, accumulator, span)


def finish_with_error(span: Span, error: BaseException) -> None:
    """Record a provider exception on ``span`` and end it. Never raises."""
    try:
        record_provider_error(span, error)
        span.end()
    except Exception:
        logger.debug("Failed to record provider error", exc_info=True)


def wrap_sync_method(
    original: Callable[..., Any],
    endpoint: Endpoint,
    get_client: ClientGetter,
) -> Callable[..., Any]:
    """Return a traced version of a synchronous provider method."""

    @functools.wraps(original)
    def traced(*args: Any, **kwargs: Any) -> Any:
        prompt = pop_prompt(kwargs)
        span = start_llm_span(get_client, endpoint, kwargs, prompt)
        if span is None:
            return original(*args, **kwargs)
        try:
            result = original(*args, **kwargs)
        except BaseException as exc:
            finish_with_error(span, exc)
            raise
        return finish_with_response(span, endpoint, result)

    setattr(traced, WRAPPED_MARKER, True)
    return traced


def wrap_async_method(
    original: Callable[..., Any],
    endpoint: Endpoint,
    get_client: ClientGetter,
) -> Callable[..., Any]:
    """Return a traced version of an ``async`` provider method."""

    @functools.wraps(original)
    async def traced(*args: Any, **kwargs: Any) -> Any:
        prompt = pop_prompt(kwargs)
        span = start_llm_span(get_client, endpoint, kwargs, prompt)
        if span is None:
            return await original(*args, **kwargs)
        try:
            result = await original(*args, **kwargs)
        except BaseException as exc:
            finish_with_error(span, exc)
            raise
        return finish_with_response(span, endpoint, result)

    setattr(traced, WRAPPED_MARKER, True)
    return traced


def patch_method(
    resource: Any,
    attribute: str,
    make_wrapper: Callable[[Callable[..., Any]], Callable[..., Any]],
) -> None:
    """Replace ``resource.attribute`` with a wrapped version, at most once."""
    original = getattr(resource, attribute, None)
    if original is None or getattr(original, WRAPPED_MARKER, False):
        return
    setattr(resource, attribute, make_wrapper(original))


class _StreamProxyBase:
    """State shared by the sync and async stream proxies."""

    def __init__(self, inner: Any, accumulator: StreamAccumulator, span: Span) -> None:
        self._inner = inner
        self._accumulator = accumulator
        self._span = span
        self._finished = False

    def __getattr__(self, name: str) -> Any:
        # Only called for attributes not found on the proxy itself, so the
        # provider stream's public API (``response``, ``request_id``...) stays
        # available to callers and to the providers' own stream helpers.
        if name == "_inner":
            raise AttributeError(name)
        return getattr(self._inner, name)

    def _observe(self, event: Any) -> None:
        try:
            self._accumulator.on_event(event, self._span)
        except Exception:
            logger.debug("Failed to observe stream event", exc_info=True)

    def finalize(self, error: BaseException | None = None) -> None:
        """End the span once, recording accumulated data and any error."""
        if self._finished:
            return
        self._finished = True
        try:
            self._accumulator.finish(self._span)
        except Exception:
            logger.debug("Failed to summarize stream", exc_info=True)
        if error is not None:
            finish_with_error(self._span, error)
        else:
            self._span.end()


class SyncStreamProxy(_StreamProxyBase):
    """Transparent wrapper around a provider's synchronous ``Stream``.

    The span ends when the stream is exhausted, closed, or raises.
    """

    def __iter__(self) -> Iterator[Any]:
        return self

    def __next__(self) -> Any:
        try:
            event = next(self._inner)
        except StopIteration:
            self.finalize()
            raise
        except BaseException as exc:
            self.finalize(exc)
            raise
        self._observe(event)
        return event

    def close(self) -> None:
        """Close the underlying stream and end the span."""
        try:
            self._inner.close()
        finally:
            self.finalize()

    def __enter__(self) -> SyncStreamProxy:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        # An exception raised by the caller's own code inside the ``with``
        # block is not a provider failure, so the span simply ends here.
        self.close()


class AsyncStreamProxy(_StreamProxyBase):
    """Transparent wrapper around a provider's ``AsyncStream``."""

    def __aiter__(self) -> AsyncIterator[Any]:
        return self

    async def __anext__(self) -> Any:
        try:
            event = await self._inner.__anext__()
        except StopAsyncIteration:
            self.finalize()
            raise
        except BaseException as exc:
            self.finalize(exc)
            raise
        self._observe(event)
        return event

    async def close(self) -> None:
        """Close the underlying stream and end the span."""
        try:
            await self._inner.close()
        finally:
            self.finalize()

    async def __aenter__(self) -> AsyncStreamProxy:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        # An exception raised by the caller's own code inside the ``with``
        # block is not a provider failure, so the span simply ends here.
        await self.close()

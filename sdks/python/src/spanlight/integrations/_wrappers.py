"""Generic wrappers that run one provider method inside an ``llm`` span.

The span starts before the call, records the response (or wraps a streaming
response in a proxy) and records provider errors without altering them.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Any

from .._span import Span
from ._common import (
    WRAPPED_MARKER,
    ClientGetter,
    Endpoint,
    finish_with_error,
    pop_prompt,
    start_llm_span,
)
from ._streams import AsyncStreamProxy, StreamAccumulator, SyncStreamProxy

logger = logging.getLogger("spanlight")


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

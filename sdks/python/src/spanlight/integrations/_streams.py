"""Observing streamed provider responses.

A :class:`StreamAccumulator` collects one stream's events; the stream proxies
wrap the provider's ``Stream``/``AsyncStream`` so every event passes through
the accumulator as the caller consumes it, and end the span once the stream
is exhausted, closed or fails.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterator
from types import TracebackType
from typing import Any

from .._span import Span
from ._common import finish_with_error

logger = logging.getLogger("spanlight")


class StreamAccumulator(ABC):
    """Collects streamed events and writes the final result onto a span."""

    @abstractmethod
    def on_event(self, event: Any, span: Span) -> None:
        """Observe one streamed event (e.g. to mark time-to-first-token)."""

    @abstractmethod
    def finish(
        self,
        span: Span,
        *,
        closed_early: bool = False,
        error: BaseException | None = None,
    ) -> None:
        """Record the accumulated model, usage and output on ``span``.

        ``closed_early`` is set when the caller closed the stream before
        exhausting it; ``error`` is the exception that ended it, if any. An
        accumulator whose final usage event never arrived uses them to say why
        the output count is unknown (``usage.unreported_reason``).
        """


def unreported_reason(closed_early: bool, error: BaseException | None, *, otherwise: str) -> str:
    """Why a stream has no final usage (``usage.unreported_reason``).

    The error that ended it wins, then an early close by the caller; ``otherwise`` names
    the provider-specific cause when the stream ran to its end.
    """
    if error is not None:
        return "stream_error"
    if closed_early:
        return "client_disconnected"
    return otherwise


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

    def finalize(self, error: BaseException | None = None, *, closed_early: bool = False) -> None:
        """End the span once, recording accumulated data and any error.

        ``closed_early`` means the caller closed the stream before it was
        exhausted; the accumulator records why the final usage is missing.
        """
        if self._finished:
            return
        self._finished = True
        try:
            self._accumulator.finish(self._span, closed_early=closed_early, error=error)
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
        """Close the underlying stream and end the span.

        Exhausting the stream already finalized the span; otherwise the caller
        stopped reading early, which the span records as such.
        """
        try:
            self._inner.close()
        finally:
            self.finalize(closed_early=True)

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
        """Close the underlying stream and end the span.

        Exhausting the stream already finalized the span; otherwise the caller
        stopped reading early, which the span records as such.
        """
        try:
            await self._inner.close()
        finally:
            self.finalize(closed_early=True)

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

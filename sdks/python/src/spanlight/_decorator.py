"""Implementation of the ``@observe`` decorator.

The decorator supports four kinds of callables, each needing slightly
different treatment so that the span covers the *real* duration of the work:

* plain functions: the span covers the call;
* coroutine functions: the span covers the awaited coroutine;
* generator functions: the span stays open until the generator is exhausted,
  closed or fails, and the current-span context is active only while the
  generator body runs (never leaking into the consumer between items);
* async generator functions: same as generators, for ``async for``.

In every case user exceptions propagate unchanged, and failures inside the
SDK itself are swallowed so they cannot break the decorated function.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
from collections.abc import AsyncGenerator, Callable, Generator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar, cast

from ._context import get_current_span, restore_current_span, set_current_span
from ._serialize import MAX_ITEMS
from ._span import Span, SpanKind

if TYPE_CHECKING:
    from ._client import Spanlight

logger = logging.getLogger("spanlight")

P = ParamSpec("P")
R = TypeVar("R")

_IMPLICIT_RECEIVERS = frozenset({"self", "cls"})


@dataclass(frozen=True)
class _ObserveOptions:
    name: str
    kind: SpanKind
    capture_input: bool
    capture_output: bool


def build_observe(
    get_client: Callable[[], Spanlight],
    name: Callable[P, R] | str | None,
    *,
    kind: SpanKind,
    capture_input: bool,
    capture_output: bool,
) -> Callable[P, R] | Callable[[Callable[P, R]], Callable[P, R]]:
    """Shared implementation behind both ``observe`` entry points.

    ``name`` is either the span name (``@observe(name="answer")`` or
    ``@observe("answer")``), ``None`` (``@observe()``), or, for the bare
    ``@observe`` form, the decorated function itself.
    """
    span_name = name if isinstance(name, str) else None

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        options = _ObserveOptions(
            name=span_name or str(getattr(func, "__name__", "function")),
            kind=kind,
            capture_input=capture_input,
            capture_output=capture_output,
        )
        return _decorate(func, get_client, options)

    if callable(name):
        return decorator(name)
    return decorator


def _decorate(
    func: Callable[P, R],
    get_client: Callable[[], Spanlight],
    options: _ObserveOptions,
) -> Callable[P, R]:
    signature = _safe_signature(func)

    def start(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Span | None:
        """Start the span for one call, or return ``None`` to run untraced."""
        try:
            client = get_client()
            if not client.enabled:
                return None
            span = client.start_span(options.name, kind=options.kind)
            if options.capture_input:
                span.set_input(_bind_arguments(signature, args, kwargs))
            return span
        except Exception:
            logger.debug("observe: failed to start span %r", options.name, exc_info=True)
            return None

    if inspect.isasyncgenfunction(func):
        return cast("Callable[P, R]", _wrap_async_generator(func, start, options))
    if inspect.isgeneratorfunction(func):
        return cast("Callable[P, R]", _wrap_generator(func, start, options))
    if inspect.iscoroutinefunction(func):
        return cast("Callable[P, R]", _wrap_coroutine(func, start, options))
    return _wrap_function(func, start, options)


_Starter = Callable[[tuple[Any, ...], dict[str, Any]], Span | None]


def _wrap_function(
    func: Callable[P, R],
    start: _Starter,
    options: _ObserveOptions,
) -> Callable[P, R]:
    @functools.wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        span = start(args, kwargs)
        if span is None:
            return func(*args, **kwargs)

        previous = get_current_span()
        token = set_current_span(span)
        try:
            result = func(*args, **kwargs)
        except BaseException as exc:
            _finish(span, error=exc)
            raise
        finally:
            restore_current_span(token, previous)
        _finish(span, output=result, capture_output=options.capture_output)
        return result

    return wrapper


def _wrap_coroutine(
    func: Callable[..., Any],
    start: _Starter,
    options: _ObserveOptions,
) -> Callable[..., Any]:
    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        span = start(args, kwargs)
        if span is None:
            return await func(*args, **kwargs)

        previous = get_current_span()
        token = set_current_span(span)
        try:
            result = await func(*args, **kwargs)
        except BaseException as exc:
            _finish(span, error=exc)
            raise
        finally:
            restore_current_span(token, previous)
        _finish(span, output=result, capture_output=options.capture_output)
        return result

    return wrapper


def _wrap_generator(
    func: Callable[..., Generator[Any, Any, Any]],
    start: _Starter,
    options: _ObserveOptions,
) -> Callable[..., Generator[Any, Any, Any]]:
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Generator[Any, Any, Any]:
        span = start(args, kwargs)
        generator = func(*args, **kwargs)
        if span is None:
            return generator
        return _traced_generator(generator, span, options)

    return wrapper


def _traced_generator(
    generator: Generator[Any, Any, Any],
    span: Span,
    options: _ObserveOptions,
) -> Generator[Any, Any, Any]:
    """Drive ``generator`` step by step with ``span`` active only during each step.

    ``send()``, ``throw()`` and ``close()`` from the consumer are forwarded
    to the inner generator, so the wrapper is transparent.
    """
    collected = _OutputCollector(enabled=options.capture_output)
    step: Callable[[], Any] = functools.partial(generator.send, None)
    while True:
        previous = get_current_span()
        token = set_current_span(span)
        try:
            item = step()
        except StopIteration as stop:
            _finish(
                span,
                output=collected.result(stop.value),
                capture_output=options.capture_output,
                output_cut=collected.truncated,
            )
            return stop.value
        except BaseException as exc:
            _finish(span, error=exc)
            raise
        finally:
            restore_current_span(token, previous)

        collected.add(item)
        try:
            sent = yield item
        except GeneratorExit:
            try:
                generator.close()
            finally:
                _finish(
                    span,
                    output=collected.result(None),
                    capture_output=options.capture_output,
                    output_cut=collected.truncated,
                )
            raise
        except BaseException as exc:
            step = functools.partial(generator.throw, exc)
        else:
            step = functools.partial(generator.send, sent)


def _wrap_async_generator(
    func: Callable[..., AsyncGenerator[Any, Any]],
    start: _Starter,
    options: _ObserveOptions,
) -> Callable[..., AsyncGenerator[Any, Any]]:
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> AsyncGenerator[Any, Any]:
        span = start(args, kwargs)
        generator = func(*args, **kwargs)
        if span is None:
            return generator
        return _traced_async_generator(generator, span, options)

    return wrapper


async def _traced_async_generator(
    generator: AsyncGenerator[Any, Any],
    span: Span,
    options: _ObserveOptions,
) -> AsyncGenerator[Any, Any]:
    """Async counterpart of :func:`_traced_generator`."""
    collected = _OutputCollector(enabled=options.capture_output)
    step: Callable[[], Any] = functools.partial(generator.asend, None)
    # A cancellation thrown in at a `yield` means the consumer abandoned the generator: asyncio
    # closes one left behind by `async for ... break` that way at loop shutdown. Like a `break`
    # over a sync generator, that ends the span as ok, not failed.
    abandoned: BaseException | None = None
    while True:
        previous = get_current_span()
        token = set_current_span(span)
        try:
            item = await step()
        except StopAsyncIteration:
            _finish(
                span,
                output=collected.result(None),
                capture_output=options.capture_output,
                output_cut=collected.truncated,
            )
            return
        except BaseException as exc:
            if exc is abandoned:
                _finish(
                    span,
                    output=collected.result(None),
                    capture_output=options.capture_output,
                    output_cut=collected.truncated,
                )
            else:
                _finish(span, error=exc)
            raise
        finally:
            restore_current_span(token, previous)

        collected.add(item)
        try:
            sent = yield item
        except GeneratorExit:
            try:
                await generator.aclose()
            finally:
                _finish(
                    span,
                    output=collected.result(None),
                    capture_output=options.capture_output,
                    output_cut=collected.truncated,
                )
            raise
        except BaseException as exc:
            abandoned = exc if isinstance(exc, asyncio.CancelledError) else None
            step = functools.partial(generator.athrow, exc)
        else:
            step = functools.partial(generator.asend, sent)


class _OutputCollector:
    """Accumulates yielded items (bounded) to use as a generator span's output."""

    def __init__(self, *, enabled: bool) -> None:
        self._enabled = enabled
        self._items: list[Any] = []
        self.truncated = False

    def add(self, item: Any) -> None:
        if not self._enabled:
            return
        if len(self._items) < MAX_ITEMS:
            self._items.append(item)
        else:
            self.truncated = True

    def result(self, return_value: Any) -> Any:
        """Return the items, joined into one string when they are all strings."""
        if not self._enabled:
            return None
        if self._items and all(isinstance(item, str) for item in self._items):
            return "".join(self._items)
        if not self._items and return_value is not None:
            return return_value
        return list(self._items)


def _finish(
    span: Span,
    *,
    output: Any = None,
    capture_output: bool = False,
    output_cut: bool = False,
    error: BaseException | None = None,
) -> None:
    """End ``span`` recording either its output or the error. Never raises."""
    try:
        if error is not None:
            if not isinstance(error, GeneratorExit):
                span.record_error(error)
        elif capture_output:
            span.set_output(output)
            # The collector kept only the first MAX_ITEMS yielded items.
            span._output_cut = span._output_cut or output_cut
        span.end()
    except Exception:
        logger.debug("observe: failed to finish span", exc_info=True)


def _safe_signature(func: Callable[..., Any]) -> inspect.Signature | None:
    try:
        return inspect.signature(func)
    except (TypeError, ValueError):
        return None


def _bind_arguments(
    signature: inspect.Signature | None,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Map call arguments to parameter names, omitting ``self``/``cls``."""
    if signature is None:
        return {"args": list(args), "kwargs": kwargs}
    try:
        bound = signature.bind_partial(*args, **kwargs)
    except TypeError:
        return {"args": list(args), "kwargs": kwargs}
    arguments = dict(bound.arguments)
    parameters = list(signature.parameters)
    if parameters and parameters[0] in _IMPLICIT_RECEIVERS:
        arguments.pop(parameters[0], None)
    return arguments

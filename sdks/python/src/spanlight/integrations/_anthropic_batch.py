"""Instrumentation of ``messages.batches.results`` in the ``anthropic`` SDK.

``results(message_batch_id)`` returns a JSONL decoder yielding one
``MessageBatchIndividualResponse`` per request. The decoder is wrapped in a proxy that
hands every item to :func:`~spanlight.integrations._batch.record_batch_item` as the user
iterates, then yields the item unchanged. Recording never raises: an item the SDK cannot
read is skipped and iteration goes on.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable, Mapping
from typing import Any

from ._anthropic_message import PROVIDER, usage_fields
from ._batch import SUCCEEDED, record_batch_item
from ._common import WRAPPED_MARKER, ClientGetter, get_field, patch_method

logger = logging.getLogger("spanlight")

Observer = Callable[[Any], None]


def patch_batch_results(messages: Any, get_client: ClientGetter, *, is_async: bool) -> None:
    """Patch ``messages.batches.results`` (sync or async) to record every result read."""
    batches = getattr(messages, "batches", None)
    if batches is None:
        return
    wrap = _wrap_async_results if is_async else _wrap_sync_results
    patch_method(batches, "results", lambda original: wrap(original, get_client))


def _wrap_sync_results(
    original: Callable[..., Any], get_client: ClientGetter
) -> Callable[..., Any]:
    @functools.wraps(original)
    def results(*args: Any, **kwargs: Any) -> Any:
        decoder = original(*args, **kwargs)
        observe = _observer(get_client, args, kwargs)
        return decoder if observe is None else _SyncResultsProxy(decoder, observe)

    setattr(results, WRAPPED_MARKER, True)
    return results


def _wrap_async_results(
    original: Callable[..., Any], get_client: ClientGetter
) -> Callable[..., Any]:
    @functools.wraps(original)
    async def results(*args: Any, **kwargs: Any) -> Any:
        decoder = await original(*args, **kwargs)
        observe = _observer(get_client, args, kwargs)
        return decoder if observe is None else _AsyncResultsProxy(decoder, observe)

    setattr(results, WRAPPED_MARKER, True)
    return results


def _observer(
    get_client: ClientGetter, args: tuple[Any, ...], kwargs: Mapping[str, Any]
) -> Observer | None:
    """The per-item recorder for one ``results`` call, or ``None`` without a batch id."""
    batch_id = args[0] if args else kwargs.get("message_batch_id")
    if not isinstance(batch_id, str) or not batch_id:
        return None
    return functools.partial(_record_item, get_client, batch_id)


def _record_item(get_client: ClientGetter, batch_id: str, item: Any) -> None:
    try:
        custom_id = get_field(item, "custom_id")
        result = get_field(item, "result")
        result_type = get_field(result, "type")
        if not isinstance(custom_id, str) or not isinstance(result_type, str):
            logger.debug("Skipping unreadable batch result in %r", batch_id)
            return
        message = get_field(result, "message") if result_type == SUCCEEDED else None
        model = get_field(message, "model")
        stop_reason = get_field(message, "stop_reason")
        error_type = get_field(result, "error", "error", "type")
        error_message = get_field(result, "error", "error", "message")
        record_batch_item(
            get_client(),
            provider=PROVIDER,
            batch_id=batch_id,
            custom_id=custom_id,
            result_type=result_type,
            model=model if isinstance(model, str) else None,
            usage=usage_fields(get_field(message, "usage")),
            output=None
            if message is None
            else {"role": "assistant", "content": get_field(message, "content") or []},
            finish_reason=stop_reason if isinstance(stop_reason, str) else None,
            error=error_type if isinstance(error_type, str) else None,
            error_message=error_message if isinstance(error_message, str) else None,
        )
    except Exception:
        logger.debug("Failed to record a batch result of %r", batch_id, exc_info=True)


class _ResultsProxyBase:
    def __init__(self, inner: Any, observe: Observer) -> None:
        self._inner = inner
        self._observe = observe

    def __getattr__(self, name: str) -> Any:
        if name == "_inner":
            raise AttributeError(name)
        return getattr(self._inner, name)


class _SyncResultsProxy(_ResultsProxyBase):
    """Stands in for ``anthropic.JSONLDecoder``; records each item it yields."""

    def __iter__(self) -> _SyncResultsProxy:
        return self

    def __next__(self) -> Any:
        item = next(self._inner)
        self._observe(item)
        return item


class _AsyncResultsProxy(_ResultsProxyBase):
    """Stands in for ``anthropic.AsyncJSONLDecoder``; records each item it yields."""

    def __aiter__(self) -> _AsyncResultsProxy:
        return self

    async def __anext__(self) -> Any:
        item = await self._inner.__anext__()
        self._observe(item)
        return item

"""Instrumentation for the official ``openai`` Python SDK.

:func:`wrap_openai` patches ``chat.completions.create`` and
``responses.create`` on one client instance (sync or async). Streaming calls
are supported; the providers' ``.stream()`` helpers go through ``create`` and
are therefore traced too.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from .._span import Span
from ._common import ClientGetter, Endpoint, as_int, client_getter, get_field, patch_method
from ._modifiers import has_request_option
from ._openai_response import (
    chat_output,
    first_str,
    record_chat_usage,
    record_response_id,
    record_service_tier,
    responses_output,
    usage_fields,
    web_search_calls,
)
from ._openai_stream import ChatStreamAccumulator, ResponsesStreamAccumulator
from ._streams import StreamAccumulator
from ._wrappers import wrap_async_method, wrap_sync_method

if TYPE_CHECKING:
    from .._client import Spanlight

logger = logging.getLogger("spanlight")

ClientT = TypeVar("ClientT")

# Azure clients, recognised by class name so the module never imports `openai` at load time.
_AZURE_CLIENTS = frozenset({"AzureOpenAI", "AsyncAzureOpenAI"})


def wrap_openai(
    client: ClientT, *, spanlight: Spanlight | None = None, provider: str | None = None
) -> ClientT:
    """Instrument an ``OpenAI``/``AsyncOpenAI`` client (or Azure variant) in place.

    Every ``chat.completions.create`` and ``responses.create`` call becomes an
    ``llm`` span with the model, token usage, time-to-first-token (streams),
    sanitized request parameters and the response. Errors are recorded and
    re-raised unchanged. Wrapping the same client twice is harmless.

    Args:
        client: The OpenAI client to instrument.
        spanlight: Spanlight client to report to; defaults to the global client at call time.
        provider: Provider name recorded on spans. Defaults to ``"azure_openai"`` for an
            ``AzureOpenAI``/``AsyncAzureOpenAI`` client (Azure bills its own rates, so the server
            does not price it at OpenAI's) and ``"openai"`` otherwise. Override it when pointing
            the OpenAI SDK at a compatible API (e.g. ``"openrouter"``).

    Returns:
        The same client object, for convenient chaining.
    """
    try:
        _instrument(client, spanlight, provider or _default_provider(client))
    except Exception:
        logger.warning("Spanlight: could not instrument OpenAI client", exc_info=True)
    return client


def _default_provider(client: object) -> str:
    """``azure_openai`` for an Azure client or a subclass of one, ``openai`` otherwise."""
    is_azure = any(cls.__name__ in _AZURE_CLIENTS for cls in type(client).__mro__)
    return "azure_openai" if is_azure else "openai"


def _instrument(client: Any, spanlight: Spanlight | None, provider: str) -> None:
    from openai import AsyncStream, Stream
    from openai.resources.chat.completions import AsyncCompletions
    from openai.resources.responses import AsyncResponses

    get_client = client_getter(spanlight)
    stream_types = (Stream, AsyncStream)
    endpoints: list[tuple[Any, Endpoint]] = [
        (client.chat.completions, ChatCompletionsEndpoint(provider, stream_types)),
        (client.responses, ResponsesEndpoint(provider, stream_types)),
    ]
    async_resources = (AsyncCompletions, AsyncResponses)
    for resource, endpoint in endpoints:
        _patch_create(
            resource, endpoint, get_client, is_async=isinstance(resource, async_resources)
        )


def _patch_create(
    resource: Any,
    endpoint: Endpoint,
    get_client: ClientGetter,
    *,
    is_async: bool,
) -> None:
    wrap = wrap_async_method if is_async else wrap_sync_method
    patch_method(resource, "create", lambda original: wrap(original, endpoint, get_client))


# ---------------------------------------------------------------------- #
# Chat Completions
# ---------------------------------------------------------------------- #


class ChatCompletionsEndpoint(Endpoint):
    """``chat.completions.create``."""

    operation = "chat"

    def __init__(self, provider: str, stream_types: tuple[type, ...]) -> None:
        super().__init__(provider)
        self._stream_types = stream_types

    def is_stream(self, result: Any) -> bool:
        return isinstance(result, self._stream_types)

    def new_accumulator(self) -> StreamAccumulator:
        return ChatStreamAccumulator(self.provider)

    def request_attributes(self, request: Mapping[str, Any]) -> dict[str, Any]:
        # Chat completions report search results as message annotations, which are not
        # a count of billed searches, so the fee cannot be priced from the response.
        if has_request_option(request, "web_search_options"):
            return {"usage.fee_unknown": "openai_chat_web_search"}
        return {}

    def record_response(self, span: Span, response: Any) -> None:
        span.set_model(get_field(response, "model"), provider=self.provider)
        record_chat_usage(span, get_field(response, "usage"))
        record_service_tier(span, response)
        record_response_id(span, response)

        output, finish_reasons = chat_output(response)
        if finish_reasons:
            span.set_attribute("response.finish_reasons", finish_reasons)
            span.set_finish_reason(first_str(finish_reasons))
        span.set_output(output)


# ---------------------------------------------------------------------- #
# Responses API
# ---------------------------------------------------------------------- #


class ResponsesEndpoint(Endpoint):
    """``responses.create``."""

    operation = "responses"

    def __init__(self, provider: str, stream_types: tuple[type, ...]) -> None:
        super().__init__(provider)
        self._stream_types = stream_types

    def is_stream(self, result: Any) -> bool:
        return isinstance(result, self._stream_types)

    def new_accumulator(self) -> StreamAccumulator:
        return ResponsesStreamAccumulator(self)

    def record_response(self, span: Span, response: Any) -> None:
        span.set_model(get_field(response, "model"), provider=self.provider)
        record_response_id(span, response)

        usage = get_field(response, "usage")
        if usage is not None:
            span.set_usage(**usage_fields(usage))
            reasoning = as_int(get_field(usage, "output_tokens_details", "reasoning_tokens"))
            if reasoning:
                span.set_attribute("usage.reasoning_tokens", reasoning)
        record_service_tier(span, response)
        span.set_usage(web_search_requests=web_search_calls(response))

        status = get_field(response, "status")
        if isinstance(status, str):
            span.set_attribute("response.status", status)
        if status == "failed":
            message = get_field(response, "error", "message")
            span.record_error(message if isinstance(message, str) else "response failed")

        span.set_output(responses_output(response))


__all__ = ["wrap_openai"]

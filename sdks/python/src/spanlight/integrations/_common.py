"""Provider-agnostic plumbing shared by the OpenAI and Anthropic wrappers.

Each instrumented endpoint is described by an :class:`Endpoint`, which knows
how to name the span and how to read a provider response. This module starts
the span, reads response fields and records provider errors without altering
them; ``_wrappers`` runs a provider method inside the span and ``_streams``
observes streaming responses as the caller consumes them.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from .._request_hash import request_hash
from .._serialize import to_jsonable
from .._span import Span
from ._modifiers import SENTINEL_TYPE_NAMES, pricing_modifier_unknown
from ._provider_errors import provider_request_id, record_response_details

if TYPE_CHECKING:
    from .._client import Spanlight
    from ._streams import StreamAccumulator

logger = logging.getLogger("spanlight")

ClientGetter = Callable[[], "Spanlight"]

WRAPPED_MARKER = "__spanlight_wrapped__"

# Request options that configure transport/auth rather than describe the
# prompt. They are never recorded: headers in particular may carry secrets.
_EXCLUDED_REQUEST_KEYS = frozenset(
    {"api_key", "extra_headers", "extra_query", "extra_body", "timeout", "http_client"}
)
_RAW_RESPONSE_HEADER = "x-stainless-raw-response"

# Keyword a wrapped call accepts to link its span to a compiled prompt. It is removed from the
# call before the provider SDK sees it.
PROMPT_KWARG = "spanlight_prompt"


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

    def request_attributes(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Attributes the request alone determines, set when the span starts."""
        return {}

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
        if type(value).__name__ in SENTINEL_TYPE_NAMES:
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
    """Mark ``span`` failed with details from a provider SDK exception.

    Besides the status code and request id, an exception carrying the provider's response adds
    the error type and code, the retry-after wait and the allowlisted rate-limit headers. A
    response the Spanlight gateway made itself records its code and no ``provider.*`` field.
    """
    span.record_error(error)
    status_code = as_int(getattr(error, "status_code", None))
    if status_code is not None:
        span.set_attribute("http.status_code", status_code)
    request_id = provider_request_id(error)
    if request_id is not None:
        span.set_attribute("provider.request_id", request_id)
    record_response_details(span, error)


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
        modifier = pricing_modifier_unknown(kwargs)
        if modifier is not None:
            span.set_attribute("usage.pricing_modifier_unknown", modifier)
        span.set_attributes(endpoint.request_attributes(kwargs))
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


def finish_with_error(span: Span, error: BaseException) -> None:
    """Record a provider exception on ``span`` and end it. Never raises.

    A call that failed before any usage came back was priced at no rate, so it drops the
    ``usage.pricing_modifier_unknown`` attribute set when it started: like the gateway, only a
    span that carries usage names a modifier it was priced without. A stream that failed after
    its usage arrived keeps it.
    """
    try:
        record_provider_error(span, error)
        if span.input_tokens is None:
            span.attributes.pop("usage.pricing_modifier_unknown", None)
        span.end()
    except Exception:
        logger.debug("Failed to record provider error", exc_info=True)


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

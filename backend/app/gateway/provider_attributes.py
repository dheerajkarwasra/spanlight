"""The provider attributes of a gateway span: what answered, why it failed, and the limits seen.

Pure: everything comes from the `GatewayResult` and the context's route and credentials, values
already in memory when the span is built, so the gateway's hot path gains no database round trip.
`spanlight.gateway.status` is always set. A gateway-made error records its `spanlight_code`; a
passed-through provider error records the provider's type and code instead.

`result.headers` are the provider's only when the answer was the provider's (a success, or its
error passed through). For a gateway-made error they are the rendered error's own headers, and
a cache hit reached no provider, so the request id and the rate-limit snapshot are left out: a
gateway `Retry-After` is never recorded as the provider's.
"""

from typing import Any

from app.gateway import attempt_log
from app.gateway.adapters.base import UPSTREAM_REQUEST_ID_HEADER
from app.gateway.context import UPSTREAM_ERROR_CODE, GatewayContext, GatewayResult
from app.gateway.upstream_io import UPSTREAM_ERROR_TYPE
from app.ingest.ratelimit_headers import snapshot_headers


def build(result: GatewayResult, context: GatewayContext) -> dict[str, Any]:
    """The `spanlight.gateway.*` status attributes and the `provider.*` attributes of a call."""
    attributes: dict[str, Any] = {"spanlight.gateway.status": result.status}
    attributes.update(_error_attributes(result))
    if _provider_answered(result):
        attributes.update(_header_attributes(result))
    if result.attempts:
        attributes["spanlight.gateway.attempt_log"] = attempt_log.build(
            result.attempts, target_names(context)
        )
    return attributes


def target_names(context: GatewayContext) -> dict[int, str | None]:
    """Each route target's credential name by target index; None for an unknown credential."""
    names: dict[int, str | None] = {}
    for index, target in enumerate(context.route.targets):
        credential = context.credentials.get(target.credential_id)
        names[index] = credential.name if credential is not None else None
    return names


def _provider_answered(result: GatewayResult) -> bool:
    if result.cache == "hit":
        return False  # answered from the cache: no provider spoke on this call
    return result.error is None or result.error.spanlight_code == UPSTREAM_ERROR_CODE


def _error_attributes(result: GatewayResult) -> dict[str, Any]:
    error = result.error
    if error is None:
        return {}
    if error.spanlight_code != UPSTREAM_ERROR_CODE:
        return {"spanlight.gateway.error_code": error.spanlight_code}
    kind = error.openai  # an upstream error carries the provider's kind in both envelopes
    if kind is None:
        return {}
    attributes: dict[str, Any] = {}
    if kind.type != UPSTREAM_ERROR_TYPE:  # the provider named its error type
        attributes["provider.error.type"] = kind.type
    if kind.code is not None:
        attributes["provider.error.code"] = kind.code
    return attributes


def _header_attributes(result: GatewayResult) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    request_id = result.headers.get(UPSTREAM_REQUEST_ID_HEADER)
    if request_id is not None:
        attributes["provider.request_id"] = request_id
    snapshot = snapshot_headers(result.headers, failed=result.status >= 400)
    if snapshot:
        attributes["provider.ratelimit"] = snapshot
    return attributes

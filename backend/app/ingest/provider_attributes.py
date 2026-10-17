"""The `provider.*` span attributes: filtered at ingest, then read as typed fields. Pure.

Any client can send these attributes (the gateway, the SDK, or a native or OTLP exporter of its
own), so ingest filters them whoever sent them. A value of the wrong shape is dropped, never
rejected: the span is stored without that key. `provider.ratelimit` goes through the header
allowlist (`ratelimit_headers`), so only the documented rate-limit headers survive.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.ingest.ratelimit_headers import snapshot_headers

ERROR_TYPE = "provider.error.type"
ERROR_CODE = "provider.error.code"
REQUEST_ID = "provider.request_id"
RATELIMIT = "provider.ratelimit"
RETRY_AFTER_S = "provider.retry_after_s"
GATEWAY_STATUS = "spanlight.gateway.status"
GATEWAY_ERROR_CODE = "spanlight.gateway.error_code"

MAX_ERROR_CHARS = 64
MAX_REQUEST_ID_CHARS = 128
MIN_HTTP_STATUS = 100
MAX_HTTP_STATUS = 599

# The string attributes and their length limits.
_STRING_LIMITS = {
    ERROR_TYPE: MAX_ERROR_CHARS,
    ERROR_CODE: MAX_ERROR_CHARS,
    REQUEST_ID: MAX_REQUEST_ID_CHARS,
}


@dataclass(frozen=True, slots=True)
class ProviderFields:
    """What a span's attributes say about the provider's answer; `None` when unknown."""

    error_type: str | None = None
    error_code: str | None = None
    ratelimit: Mapping[str, str] = field(default_factory=dict)
    request_id: str | None = None
    retry_after_s: float | None = None
    gateway_error_code: str | None = None
    gateway_status: int | None = None


def clean_provider_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of `attributes` with every malformed `provider.*` value dropped.

    `provider.retry_after_s` is stored as the float it was read as. `provider.ratelimit` is kept
    only as a flat object of string values, filtered through the header allowlist with the
    failed-span rule (the whole snapshot), and dropped when nothing survives. Other attributes
    are copied unchanged.
    """
    cleaned = dict(attributes)
    for key, limit in _STRING_LIMITS.items():
        if key in cleaned and _bounded_string(cleaned[key], limit) is None:
            del cleaned[key]
    if RETRY_AFTER_S in cleaned:
        seconds = _seconds(cleaned[RETRY_AFTER_S])
        if seconds is None:
            del cleaned[RETRY_AFTER_S]
        else:
            cleaned[RETRY_AFTER_S] = seconds
    if RATELIMIT in cleaned:
        snapshot = _ratelimit(cleaned[RATELIMIT])
        if snapshot:
            cleaned[RATELIMIT] = snapshot
        else:
            del cleaned[RATELIMIT]
    return cleaned


def read_provider_fields(attributes: Mapping[str, Any]) -> ProviderFields:
    """The provider fields of a span; each is `None` (or empty) when absent or malformed."""
    return ProviderFields(
        error_type=_bounded_string(attributes.get(ERROR_TYPE), MAX_ERROR_CHARS),
        error_code=_bounded_string(attributes.get(ERROR_CODE), MAX_ERROR_CHARS),
        ratelimit=_ratelimit(attributes.get(RATELIMIT)),
        request_id=_bounded_string(attributes.get(REQUEST_ID), MAX_REQUEST_ID_CHARS),
        retry_after_s=_seconds(attributes.get(RETRY_AFTER_S)),
        gateway_error_code=_bounded_string(attributes.get(GATEWAY_ERROR_CODE), MAX_ERROR_CHARS),
        gateway_status=_http_status(attributes.get(GATEWAY_STATUS)),
    )


def _bounded_string(value: object, limit: int) -> str | None:
    return value if isinstance(value, str) and len(value) <= limit else None


def _http_status(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if MIN_HTTP_STATUS <= value <= MAX_HTTP_STATUS else None


def _seconds(value: object) -> float | None:
    """A non-negative finite number of seconds; booleans and numeric strings do not count."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        seconds = float(value)
    except OverflowError:  # an integer too large for a float
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _ratelimit(value: object) -> dict[str, str]:
    """The allowlisted snapshot of a flat string-to-string object, else an empty one."""
    if not isinstance(value, Mapping):
        return {}
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        return {}
    return snapshot_headers(value, failed=True)

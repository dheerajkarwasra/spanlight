"""Request options whose price Spanlight does not model.

The provider wrappers read these from the call's keywords or from
``extra_body``, the way callers reach parameters their SDK predates.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

# Type names of the provider SDKs' "not given" sentinels.
SENTINEL_TYPE_NAMES = frozenset({"NotGiven", "Omit"})

# A data-residency value kept verbatim in the attribute; anything else is
# reported as ``other`` so the attribute stays short and enumerable. The
# gateway applies the same bound.
_GEO_PATTERN = re.compile(r"[a-z0-9_-]{1,32}")


def pricing_modifier_unknown(request: Mapping[str, Any]) -> str | None:
    """Name the request option whose price Spanlight does not model, if any.

    Data residency (Anthropic ``inference_geo``, a surcharge for ``us``) and
    fast mode (``speed: "fast"``) change the bill. This request-side guess is
    a fallback: the server prices both from the response's ``usage.inference_geo``
    and ``usage.speed`` attributes and drops the guess when they are reported.
    Without them the span is priced at the standard rate and
    tagged with ``usage.pricing_modifier_unknown`` set to
    ``inference_geo_<value>`` or ``fast_mode``, so the rollups count it apart
    and the reconciliation report can explain the residual. When both are
    present the data-residency value wins. Options may arrive as keywords or
    inside ``extra_body`` (how callers reach parameters their SDK predates).
    ``inference_geo: "global"`` is the default routing at the standard price,
    so it sets nothing. A value that is not 1-32 characters of ``a-z``,
    ``0-9``, ``_`` or ``-`` (after trimming and lower-casing) is reported as
    ``inference_geo_other``.
    """
    geo = _request_option(request, "inference_geo")
    if geo is not None and geo != "global":
        return f"inference_geo_{geo if _GEO_PATTERN.fullmatch(geo) else 'other'}"
    if _request_option(request, "speed") == "fast":
        return "fast_mode"
    return None


def _request_option(request: Mapping[str, Any], name: str) -> str | None:
    """Return a string request option, trimmed and lower-cased, or ``None``."""
    value = _raw_request_option(request, name)
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return None


def has_request_option(request: Mapping[str, Any], name: str) -> bool:
    """Whether the call passed option ``name``, as a keyword or inside ``extra_body``."""
    return _raw_request_option(request, name) is not None


def _raw_request_option(request: Mapping[str, Any], name: str) -> Any:
    value = request.get(name)
    if value is None or type(value).__name__ in SENTINEL_TYPE_NAMES:
        extra_body = request.get("extra_body")
        value = extra_body.get(name) if isinstance(extra_body, Mapping) else None
    return value


def regional_endpoint(client: Any) -> dict[str, str]:
    """Flag a client whose base URL is an OpenAI regional host.

    Returns ``usage.pricing_modifier_unknown: regional_endpoint`` for a host
    such as ``eu.api.openai.com``, else nothing. OpenAI bills an uplift there
    on some models and no response reports the region, so the call is priced
    at the standard rate and flagged.
    """
    host = getattr(getattr(client, "base_url", None), "host", None)
    if isinstance(host, str) and host.lower().removesuffix(".").endswith(".api.openai.com"):
        return {"usage.pricing_modifier_unknown": "regional_endpoint"}
    return {}

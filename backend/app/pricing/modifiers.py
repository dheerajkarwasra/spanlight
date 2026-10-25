"""Pricing modifiers a span reports, and the flag for the ones Spanlight cannot price. Pure.

The provider's response is the truth: Anthropic reports where inference ran
(`usage.inference_geo`) and at which speed (`usage.speed`), whatever the request asked and
whatever the workspace default (`default_inference_geo`) filled in. The gateway and the SDK copy
those fields into the span's attributes, and every source keeps the request-side guess
`usage.pricing_modifier_unknown` (`inference_geo_<value>`, `fast_mode`) as a fallback for a
response that lacks them. At ingestion the reported fields replace the guess for their option,
and a reported modifier the pricing snapshot has no factor for is flagged instead, so the
rollups count it as priced at the standard rate.
"""

import re
from collections.abc import Mapping
from typing import Any

from app.pricing.tiers import TierMultipliers

FLAG = "usage.pricing_modifier_unknown"
_GEO = "usage.inference_geo"
_SPEED = "usage.speed"
# A reported value named as is; any other is `other`, so the names stay short and enumerable.
_VALUE = re.compile(r"[a-z0-9_-]{1,32}")
# The flag keeps the request-side spelling of fast mode, so its values do not change meaning.
_FLAG_NAMES = {"fast": "fast_mode"}


def reported_modifiers(attributes: Mapping[str, Any]) -> tuple[str, ...]:
    """The modifiers the response reported: `inference_geo_<value>` for a geo other than
    `global`, and the speed (`fast`) when it is not `standard`."""
    names: list[str] = []
    geo = _reported(attributes, _GEO)
    if geo is not None and geo != "global":
        names.append(f"inference_geo_{_name(geo)}")
    speed = _reported(attributes, _SPEED)
    if speed is not None and speed != "standard":
        names.append(speed if speed == "fast" else f"speed_{_name(speed)}")
    return tuple(names)


def flag_unpriced_modifiers(
    attributes: dict[str, Any],
    provider: str | None,
    modifiers: tuple[str, ...],
    multipliers: TierMultipliers,
) -> None:
    """Drop the request-side flag of an option the response reported, then flag the first
    reported modifier `multipliers` has no factor for. Mutates `attributes`."""
    flag = attributes.get(FLAG)
    if isinstance(flag, str) and (
        (flag.startswith("inference_geo_") and _reported(attributes, _GEO) is not None)
        or (flag == "fast_mode" and _reported(attributes, _SPEED) is not None)
    ):
        del attributes[FLAG]
    unpriced = next((m for m in modifiers if multipliers.modifier(provider, m) is None), None)
    if unpriced is not None and FLAG not in attributes:
        attributes[FLAG] = _FLAG_NAMES.get(unpriced, unpriced)


def _reported(attributes: Mapping[str, Any], key: str) -> str | None:
    value = attributes.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return None


def _name(value: str) -> str:
    return value if _VALUE.fullmatch(value) else "other"

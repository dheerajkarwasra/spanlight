"""Best-effort conversion of arbitrary Python values into JSON-compatible data.

Captured inputs and outputs can be anything: pydantic models returned by
provider SDKs, dataclasses, custom objects, huge strings. This module turns
them into plain ``dict``/``list``/``str``/number values that ``json.dumps`` can
always encode, while bounding size and depth so a single span can never
produce an unreasonably large payload. It never raises.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import math
import uuid
from collections.abc import Mapping
from typing import Any

MAX_DEPTH = 12
MAX_STRING_LENGTH = 32_000
MAX_ITEMS = 1_000

_TRUNCATION_MARKER = "...[truncated]"

JsonValue = Any
"""Alias documenting that a value is JSON-compatible (recursive types are awkward)."""


def to_jsonable(value: Any) -> JsonValue:
    """Convert ``value`` into a JSON-compatible structure.

    Args:
        value: Any Python object.

    Returns:
        A value composed only of ``dict``, ``list``, ``str``, ``int``,
        ``float``, ``bool`` and ``None``. Unknown objects are rendered with
        ``repr``. Oversized strings and collections are truncated.
    """
    return to_jsonable_cut(value)[0]


def to_jsonable_cut(value: Any) -> tuple[JsonValue, bool]:
    """Like :func:`to_jsonable`, plus whether anything was cut to fit the bounds."""
    cut: list[bool] = []
    try:
        return _convert(value, 0, cut), bool(cut)
    except Exception:
        return _truncate_string(_safe_repr(value), cut), bool(cut)


def _convert(value: Any, depth: int, cut: list[bool]) -> JsonValue:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, str):
        return _truncate_string(value, cut)
    if depth >= MAX_DEPTH:
        cut.append(True)
        return "[max depth reached]"

    if isinstance(value, Mapping):
        return _convert_mapping(value, depth, cut)
    if isinstance(value, list | tuple | set | frozenset):
        return _convert_sequence(list(value), depth, cut)
    if isinstance(value, bytes | bytearray | memoryview):
        return f"<{len(value)} bytes>"
    if isinstance(value, enum.Enum):
        return _convert(value.value, depth + 1, cut)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, BaseException):
        return {"type": type(value).__name__, "message": _truncate_string(str(value), cut)}

    dumped = _dump_model(value)
    if dumped is not None:
        return _convert(dumped, depth + 1, cut)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = {field.name: getattr(value, field.name) for field in dataclasses.fields(value)}
        return _convert_mapping(fields, depth, cut)

    return _truncate_string(_safe_repr(value), cut)


def _convert_mapping(value: Mapping[Any, Any], depth: int, cut: list[bool]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for index, (key, item) in enumerate(value.items()):
        if index >= MAX_ITEMS:
            result["..."] = f"{len(value) - MAX_ITEMS} more keys truncated"
            cut.append(True)
            break
        result[str(key)] = _convert(item, depth + 1, cut)
    return result


def _convert_sequence(items: list[Any], depth: int, cut: list[bool]) -> list[JsonValue]:
    converted = [_convert(item, depth + 1, cut) for item in items[:MAX_ITEMS]]
    if len(items) > MAX_ITEMS:
        converted.append(f"... {len(items) - MAX_ITEMS} more items truncated")
        cut.append(True)
    return converted


def _dump_model(value: Any) -> Any | None:
    """Return a plain representation of pydantic (v2 or v1) models, else ``None``."""
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="json", exclude_none=True)
        except Exception:
            return None
    legacy_dict = getattr(value, "dict", None)
    if callable(legacy_dict) and hasattr(value, "__fields__"):
        try:
            return legacy_dict()
        except Exception:
            return None
    return None


def _truncate_string(value: str, cut: list[bool]) -> str:
    if len(value) <= MAX_STRING_LENGTH:
        return value
    cut.append(True)
    return value[:MAX_STRING_LENGTH] + _TRUNCATION_MARKER


def _safe_repr(value: Any) -> str:
    try:
        return repr(value)
    except Exception:
        return f"<unrepresentable {type(value).__name__}>"

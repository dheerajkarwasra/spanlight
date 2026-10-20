"""OTLP/JSON values: object members, ids, attributes, numbers and JSON-encoded content. Pure.

A malformed number is an `OtlpDecodeError` (a 400 for the request), never an exception that the
API would answer with a 500. A double that is not finite (NaN, an infinity) is kept as its
OTLP/JSON name, a string, because Postgres `jsonb` has no such numbers and refusing them there
would fail every span of the batch.
"""

import base64
import binascii
import json
import re
from collections.abc import Mapping
from typing import Any

from app.core.text import storable_double
from app.ingest.jsondepth import exceeds_depth


class OtlpDecodeError(ValueError):
    pass


class TooManySpansError(ValueError):
    """The request holds more spans than one batch may; found before any span is decoded."""


# A decimal integer as OTLP/JSON writes an int64 or uint64: ASCII digits only, at most 20 of them.
# `str.isdigit` alone also accepts "²", which `int` refuses.
_INTEGER_TEXT = re.compile(r"-?[0-9]{1,20}")

ENUM_NAMES = {
    "SPAN_KIND_UNSPECIFIED": 0,
    "SPAN_KIND_INTERNAL": 1,
    "SPAN_KIND_SERVER": 2,
    "SPAN_KIND_CLIENT": 3,
    "SPAN_KIND_PRODUCER": 4,
    "SPAN_KIND_CONSUMER": 5,
    "STATUS_CODE_UNSET": 0,
    "STATUS_CODE_OK": 1,
    "STATUS_CODE_ERROR": 2,
}


def json_int(value: Any, *, default: int = 0, enum: bool = False) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise OtlpDecodeError("expected an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        if not _INTEGER_TEXT.fullmatch(value):
            raise OtlpDecodeError("expected an integer")
        return int(value)
    if isinstance(value, str) and enum:
        # Enum fields may be sent by name, e.g. "SPAN_KIND_CLIENT".
        return ENUM_NAMES.get(value, default)
    raise OtlpDecodeError("expected an integer")


def json_double(value: Any) -> float | str:
    """A `doubleValue`: a JSON number, or a string such as "1.5", "NaN" or "-Infinity"."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise OtlpDecodeError("expected a number")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise OtlpDecodeError("expected a number") from exc
    return storable_double(number)


def _storable_float(text: str) -> float | str:
    return storable_double(float(text))  # `1e999` parses as an infinity


def parse_content(value: Any) -> Any:
    """GenAI content attributes are usually JSON encoded into a string.

    A string that is not JSON, nests deeper than `MAX_JSON_DEPTH` or holds a number Python will
    not convert (more than 4300 digits) is kept as it is. NaN, Infinity and numbers past a double
    in it become strings, as `storable_double` names them.
    """
    if isinstance(value, str):
        try:
            parsed = json.loads(value, parse_constant=str, parse_float=_storable_float)
        except (ValueError, RecursionError):
            return value
        return value if exceeds_depth(parsed) else parsed
    return value


def member(obj: Any, *keys: str) -> Any:
    """The value of the first of `keys` in the JSON object `obj`; not an object is a 400."""
    if not isinstance(obj, Mapping):
        raise OtlpDecodeError("expected a JSON object")
    for key in keys:
        if key in obj:
            return obj[key]
    return None


def member_dict(obj: Any, *keys: str) -> dict[str, Any]:
    value = member(obj, *keys)
    return value if isinstance(value, dict) else {}


def member_list(obj: Any, *keys: str) -> list[Any]:
    value = member(obj, *keys)
    return value if isinstance(value, list) else []


def json_id(value: Any, *, size: int) -> str | None:
    """OTLP/JSON ids are hex; some exporters send base64, which we also accept."""
    if not value or not isinstance(value, str):
        return None
    if len(value) == size * 2:
        try:
            bytes.fromhex(value)
        except ValueError:
            pass
        else:
            return value.lower()
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return value  # left for span validation to reject with a clear reason
    return decoded.hex() if len(decoded) == size else value


def json_attributes(entries: list[Any]) -> dict[str, Any]:
    attributes: dict[str, Any] = {}
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("key"), str):
            attributes[entry["key"]] = _json_value(entry.get("value"))
    return attributes


def _json_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return None
    if "stringValue" in value:
        return value["stringValue"]
    if "boolValue" in value:
        return bool(value["boolValue"])
    if "intValue" in value:
        return json_int(value["intValue"])
    if "doubleValue" in value:
        return json_double(value["doubleValue"])
    if "arrayValue" in value:
        return [_json_value(item) for item in member_list(value["arrayValue"], "values")]
    if "kvlistValue" in value:
        return json_attributes(member_list(value["kvlistValue"], "values"))
    if "bytesValue" in value:
        return value["bytesValue"]
    return None

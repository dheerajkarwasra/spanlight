"""Build the Gemini Generate Content payload from its public SDK arguments."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from ._common import get_field

_REQUEST_FIELDS = frozenset({"tools", "toolConfig", "safetySettings", "cachedContent", "labels"})
_SDK_FIELDS = frozenset({"http_options", "httpOptions", "abort_signal", "abortSignal"})
_RAW_VALUE_FIELDS = frozenset({"args", "response", "parametersJsonSchema", "responseJsonSchema"})


def _camel(name: str) -> str:
    first, *tail = name.split("_")
    return first + "".join(word[:1].upper() + word[1:] for word in tail)


def wire_value(value: Any) -> Any:
    """Use public model serialization and preserve arbitrary keys in JSON arguments."""
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = _camel(key)
            if name == "properties" and isinstance(item, Mapping):
                result[name] = {field: wire_value(schema) for field, schema in item.items()}
            else:
                result[name] = item if name in _RAW_VALUE_FIELDS else wire_value(item)
        return result
    if isinstance(value, list):
        return [wire_value(item) for item in value]
    if callable(value):
        return {"sdkCallable": True}
    return value


def _part(value: Any) -> Any:
    if isinstance(value, str):
        return {"text": value}
    if isinstance(value, Mapping) and "uri" in value and "mimeType" in value:
        return {"fileData": {"fileUri": value["uri"], "mimeType": value["mimeType"]}}
    return value


def _content(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping) and "parts" in value:
        return dict(value)
    parts = [_part(item) for item in (value if isinstance(value, list) else [value])]
    role = "model" if parts and get_field(parts[0], "functionCall") is not None else "user"
    return {"role": role, "parts": parts}


def _contents(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return [_content(value)]
    turns: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for item in value:
        turn = _content(item)
        already_grouped = isinstance(item, list) or (isinstance(item, Mapping) and "parts" in item)
        if already_grouped:
            turns.append(turn)
            current = None
        elif current is not None and current["role"] == turn["role"]:
            current["parts"].extend(turn["parts"])
        else:
            turns.append(turn)
            current = turn
    return turns


def request_body(request: Mapping[str, Any], *, streaming: bool) -> dict[str, Any]:
    """Record Gemini REST fields without client transport or authentication settings."""
    body: dict[str, Any] = {
        "model": request.get("model"),
        "contents": _contents(wire_value(request.get("contents"))),
    }
    if streaming:
        body["stream"] = True
    config = request.get("config")
    if config is None:
        return body

    http_options = get_field(config, "http_options") or get_field(config, "httpOptions")
    dump = getattr(config, "model_dump", None)
    if callable(dump):
        # Python mode leaves SDK-only callables intact so we can mark them without serializing
        # their names, bodies or arguments.
        values = cast(dict[str, Any], dump(mode="python", exclude_none=True, exclude=_SDK_FIELDS))
    else:
        values = dict(config)

    generation: dict[str, Any] = {}
    for field, value in values.items():
        if value is None or field in _SDK_FIELDS:
            continue
        key = _camel(field)
        if key == "systemInstruction":
            body[key] = _content(wire_value(value))
        elif key == "responseSchema" and callable(value):
            generation["sdkResponseSchema"] = True
        elif key in _REQUEST_FIELDS:
            body[key] = wire_value(value)
        else:
            generation[key] = value if key in _RAW_VALUE_FIELDS else wire_value(value)
    if any(get_field(http_options, key) is not None for key in ("extra_body", "extraBody")):
        generation["sdkExtraBody"] = True
    if generation:
        body["generationConfig"] = generation
    return body

"""Text and JSON that PostgreSQL will store. Pure: no database, no I/O.

PostgreSQL's `text` and `jsonb` refuse the NUL character, and a lone UTF-16 surrogate (which a
JSON `\\ud800` escape can produce) cannot be encoded as UTF-8. Either one in a batch insert fails
the whole statement, so text from clients, providers and webhooks passes through here first.
`jsonb` has no NaN or infinity either, which Python's `json.loads` accepts by default.
"""

import math
from typing import Any

from app.core.redact import DEPTH_MARKER, MAX_DEPTH

NUL = "\x00"


def clean_text(value: str) -> str:
    """`value` without NUL, with each lone surrogate replaced by `?`."""
    without_nul = value.replace(NUL, "")
    try:
        without_nul.encode("utf-8")
    except UnicodeEncodeError:
        return without_nul.encode("utf-8", "replace").decode("utf-8")
    return without_nul


def without_nul(value: Any) -> Any:
    """A JSON value with NUL removed from every string and key."""
    if isinstance(value, str):
        return value.replace(NUL, "")
    if isinstance(value, list):
        return [without_nul(item) for item in value]
    if isinstance(value, dict):
        return {without_nul(key): without_nul(item) for key, item in value.items()}
    return value


def clean_json(value: Any) -> Any:
    """A JSON value with every string and key passed through `clean_text`."""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, list):
        return [clean_json(item) for item in value]
    if isinstance(value, dict):
        return {clean_json(key): clean_json(item) for key, item in value.items()}
    return value


def storable_double(number: float) -> float | str:
    """`number`, or the OTLP/JSON name of a double that `jsonb` cannot hold."""
    if math.isnan(number):
        return "NaN"
    if math.isinf(number):
        return "Infinity" if number > 0 else "-Infinity"
    return number


def storable_json(value: Any, *, depth: int = 0) -> Any:
    """`clean_json(value)`, with every NaN or infinity also replaced by its `storable_double` name.

    For a JSON value parsed leniently (plain `json.loads`) that is stored as it is. A value
    nested deeper than `MAX_DEPTH` becomes `DEPTH_MARKER`, as redaction stores it: `json.loads`
    reads nesting far deeper than a recursive walk may go.
    """
    if depth >= MAX_DEPTH:
        return DEPTH_MARKER
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, float):
        return storable_double(value)
    if isinstance(value, list):
        return [storable_json(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        return {
            clean_text(key): storable_json(item, depth=depth + 1) for key, item in value.items()
        }
    return value

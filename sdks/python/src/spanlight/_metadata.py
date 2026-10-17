"""Trace metadata rules on the client: the lenient validator and the merge. Pure.

A trace carries at most 32 metadata entries. A key matches ``^[a-z0-9_.-]{1,64}$``; a value is
a string of 1 to 256 characters (code points). A number or boolean becomes its JSON text
(``42`` -> ``"42"``, ``1e3`` -> ``"1000.0"``, ``True`` -> ``"true"``); a ``None`` value drops
its key silently; anything else is invalid.

The SDK is lenient: an invalid entry is dropped with a ``DEBUG`` log naming the key (never the
value), and when more than 32 valid entries remain the first 32 in lexical (code point) key
order are kept. Nothing here raises into user code.

These rules mirror the server's ``app/ingest/metadata.py`` rule for rule; both are tested
against one table of cases, ``sdks/fixtures/metadata-cases.json``.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Mapping

logger = logging.getLogger("spanlight")

MAX_METADATA_KEYS = 32
MAX_METADATA_VALUE = 256
_KEY_PATTERN = re.compile(r"[a-z0-9_.-]{1,64}")
_SHOWN_KEY = 64


class _InvalidEntryError(Exception):
    """One metadata entry breaks a rule; the message names the rule, never the value."""


def clean_metadata(raw: object) -> dict[str, str]:
    """The valid entries of ``raw``, by the lenient rules; ``None`` or a non-mapping is empty."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        logger.debug(
            "update_trace(metadata=) needs a mapping, got %s; ignoring", type(raw).__name__
        )
        return {}
    valid: dict[str, str] = {}
    for key, value in raw.items():
        if value is None:
            continue
        try:
            valid[_checked_key(key)] = _value_text(value)
        except _InvalidEntryError as exc:
            logger.debug("Dropping trace metadata key %s: %s", _shown(key), exc)
    kept = sorted(valid)[:MAX_METADATA_KEYS]
    for key in sorted(valid)[MAX_METADATA_KEYS:]:
        logger.debug("Dropping trace metadata key %r: at most %d keys", key, MAX_METADATA_KEYS)
    return {key: valid[key] for key in kept}


def merge_metadata(current: Mapping[str, str], incoming: Mapping[str, str]) -> dict[str, str]:
    """``current`` with ``incoming`` merged in, by the server's merge rule.

    Per key the incoming value wins, and a key ``current`` has is always updatable. A new key
    is added only while there is room under 32, the new keys in lexical (code point) order.
    Nothing is ever deleted.
    """
    merged = dict(current)
    merged.update({key: value for key, value in incoming.items() if key in current})
    room = max(0, MAX_METADATA_KEYS - len(current))
    new_keys = sorted(key for key in incoming if key not in current)
    merged.update({key: incoming[key] for key in new_keys[:room]})
    for key in new_keys[room:]:
        logger.debug("Dropping trace metadata key %r: the trace has %d keys", key, len(current))
    return merged


def _checked_key(key: object) -> str:
    # `fullmatch`, not `match` with `$`: `$` alone would also accept a key ending in a newline.
    if not isinstance(key, str) or _KEY_PATTERN.fullmatch(key) is None:
        raise _InvalidEntryError(f"key must match ^{_KEY_PATTERN.pattern}$")
    return key


def _value_text(value: object) -> str:
    text = _json_text(value)
    if not 1 <= len(text) <= MAX_METADATA_VALUE:
        raise _InvalidEntryError(f"value must be 1 to {MAX_METADATA_VALUE} characters")
    return text


def _json_text(value: object) -> str:
    if isinstance(value, str):
        return value
    try:
        if isinstance(value, int):  # `bool` included: `json.dumps(True)` is "true"
            return json.dumps(value)
        if isinstance(value, float):
            # JSON has no NaN or infinity.
            if not math.isfinite(value):
                raise _InvalidEntryError("value must be a finite number")
            return json.dumps(value)
    except ValueError:  # an integer past Python's int-to-text digit limit
        raise _InvalidEntryError(f"value must be 1 to {MAX_METADATA_VALUE} characters") from None
    raise _InvalidEntryError("value must be a string, number or boolean")


def _shown(key: object) -> str:
    """How a dropped key appears in the log: its text (cut at 64), or only its type."""
    if isinstance(key, str):
        return repr(key[:_SHOWN_KEY])
    return f"of type {type(key).__name__}"

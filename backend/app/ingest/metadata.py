"""Trace metadata rules: the one validator and the one merge (spec §4.2, §4.4, §4.6). Pure.

A trace carries at most 32 metadata entries. A key matches `^[a-z0-9_.-]{1,64}$`; a value is a
string of 1 to 256 characters (code points, as received). A JSON number or boolean becomes its
JSON text (`42` -> `"42"`, `1e3` -> `"1000.0"`, `true` -> `"true"`); a `null` value drops its key
silently; an object or an array is invalid.

The native API is strict: the first violation rejects the span with a message naming the field
(`trace.metadata.<key>: <reason>` or `trace.metadata: at most 32 keys`). The OTLP mapper, the
gateway header and the SDK are lenient: each invalid entry is dropped and counted, and when more
than 32 valid entries remain the first 32 in lexical key order are kept. Counts only: a dropped
key or value is never logged.

Strings are taken as they are: callers pass JSON that has already been through
`app.core.text.clean_json` (the native pipeline, the OTLP mapper and the gateway header all do),
so no NUL character or lone surrogate reaches the database.

`merge_metadata` is the in-batch merge; `merge_trace_metadata` (migration 0800) is its SQL twin
for the cross-batch upsert, and one table of cases (`sdks/fixtures/metadata-cases.json`) tests
both.
"""

import json
import math
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Annotated, Any, Literal

from pydantic import StrictStr, StringConstraints

from app.core.observability import METADATA_DROPPED

KEY_PATTERN = r"^[a-z0-9_.-]{1,64}$"
METADATA_KEY_PATTERN = re.compile(KEY_PATTERN)
MAX_METADATA_KEYS = 32
MAX_METADATA_VALUE = 256

# The pydantic type of a metadata key in every route and schema that takes one.
MetadataKey = Annotated[StrictStr, StringConstraints(pattern=KEY_PATTERN)]

DropReason = Literal["key", "value", "type", "count"]
DropPath = Literal["otlp", "gateway"]

_FIELD = "trace.metadata"
_KEY_RULE = f"key must match {KEY_PATTERN}"
_LENGTH_RULE = f"value must be 1 to {MAX_METADATA_VALUE} characters"
_FINITE_RULE = "value must be a finite number"
_TYPE_RULE = "value must be a string, number or boolean"
_COUNT_RULE = f"{_FIELD}: at most {MAX_METADATA_KEYS} keys"
_OBJECT_RULE = f"{_FIELD}: must be an object"


@dataclass
class MetadataDrops:
    """How many metadata entries a lenient path dropped, by reason; never the entries."""

    key: int = 0
    value: int = 0
    type: int = 0
    count: int = 0

    @property
    def total(self) -> int:
        return self.key + self.value + self.type + self.count

    def add(self, reason: DropReason, number: int = 1) -> None:
        setattr(self, reason, getattr(self, reason) + number)

    def absorb(self, other: "MetadataDrops") -> None:
        """Add every count of `other` to this tally."""
        for reason, number in asdict(other).items():
            setattr(self, reason, getattr(self, reason) + number)


class _InvalidEntryError(Exception):
    """One metadata entry breaks a rule; `reason` is its drop counter, the message its rule."""

    def __init__(self, reason: DropReason, rule: str) -> None:
        super().__init__(rule)
        self.reason: DropReason = reason


def clean_metadata(raw: Any, *, strict: bool) -> tuple[dict[str, str], MetadataDrops]:
    """The valid metadata in `raw`, and what was dropped from it.

    `None` means none was sent. Strict raises `ValueError` with the contract message on the
    first violation (the 32-key cap counts after `null` values are dropped, then each entry is
    checked in lexical key order); lenient drops and counts instead, and a `raw` that is not an
    object counts as one `type` drop.
    """
    drops = MetadataDrops()
    if raw is None:
        return {}, drops
    if not isinstance(raw, Mapping):
        if strict:
            raise ValueError(_OBJECT_RULE)
        drops.add("type")
        return {}, drops
    present = {key: value for key, value in raw.items() if value is not None}
    if strict:
        return _clean_strict(present), drops
    return _clean_lenient(present, drops), drops


def _clean_strict(present: Mapping[Any, Any]) -> dict[str, str]:
    if len(present) > MAX_METADATA_KEYS:
        raise ValueError(_COUNT_RULE)
    cleaned: dict[str, str] = {}
    for key in sorted(present, key=str):
        try:
            name, text = _entry(key, present[key])
        except _InvalidEntryError as exc:
            raise ValueError(f"{_FIELD}.{key}: {exc}") from None
        cleaned[name] = text
    return cleaned


def _clean_lenient(present: Mapping[Any, Any], drops: MetadataDrops) -> dict[str, str]:
    valid: dict[str, str] = {}
    for key, value in present.items():
        try:
            name, text = _entry(key, value)
        except _InvalidEntryError as exc:
            drops.add(exc.reason)
            continue
        valid[name] = text
    kept = sorted(valid)[:MAX_METADATA_KEYS]
    drops.add("count", len(valid) - len(kept))
    return {key: valid[key] for key in kept}


def _entry(key: object, value: object) -> tuple[str, str]:
    """`(key, value as text)`, or `_InvalidEntryError` naming the first rule the entry breaks."""
    # `fullmatch`, not `match`: `$` alone would also accept a key ending in a newline.
    if not isinstance(key, str) or METADATA_KEY_PATTERN.fullmatch(key) is None:
        raise _InvalidEntryError("key", _KEY_RULE)
    text = _value_text(value)
    if not 1 <= len(text) <= MAX_METADATA_VALUE:
        raise _InvalidEntryError("value", _LENGTH_RULE)
    return key, text


def _value_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, int):  # `bool` included: `json.dumps(True)` is "true"
        return json.dumps(value)
    if isinstance(value, float):
        # JSON has no NaN or infinity, though Python's `json.loads` reads them.
        if not math.isfinite(value):
            raise _InvalidEntryError("value", _FINITE_RULE)
        return json.dumps(value)
    raise _InvalidEntryError("type", _TYPE_RULE)


def merge_metadata(current: Mapping[str, str], incoming: Mapping[str, str]) -> dict[str, str]:
    """`current` with `incoming` merged in, by the rules of spec §4.4.

    Per key the incoming value wins, and a key `current` has is always updatable. A new key is
    added only while there is room under 32, the new keys in lexical (code point) order, so the
    result does not depend on which batch arrived first. Nothing is ever deleted. Merging the
    same `incoming` again changes nothing.

    Both inputs are already cleaned (`clean_metadata`): string values only, no `null`. Neither
    this function nor its SQL twin `merge_trace_metadata` checks that; a `null` reaching the twin
    would be stored as JSON `null`.
    """
    merged = dict(current)
    merged.update({key: value for key, value in incoming.items() if key in current})
    room = max(0, MAX_METADATA_KEYS - len(current))
    added = sorted(key for key in incoming if key not in current)[:room]
    merged.update({key: incoming[key] for key in added})
    return merged


def metadata_from_attributes(
    attributes: Mapping[str, Any], *, prefix: str, drops: MetadataDrops
) -> dict[str, str]:
    """The metadata carried by the attributes named `<prefix><key>`, cleaned leniently.

    The key is the rest of the name, lower-cased (an exporter chose its spelling, spec §12); a
    key that still breaks the pattern is dropped. When several names fold to one key, the name
    that sorts last wins, which makes an all-lower-case spelling win, and each other one counts
    as a `key` drop. Drops are added to `drops`.
    """
    raw: dict[str, Any] = {}
    for name in sorted(attributes):
        value = attributes[name]
        if not name.startswith(prefix) or value is None:
            continue
        key = name[len(prefix) :].lower()
        if key in raw:
            drops.add("key")
        raw[key] = value
    cleaned, found = clean_metadata(raw, strict=False)
    drops.absorb(found)
    return cleaned


def record_metadata_drops(path: DropPath, drops: MetadataDrops) -> None:
    """Count `drops` in `spanlight_trace_metadata_dropped_total` under `path`.

    Only the reasons `MetadataDrops` holds (key, value, type, count). A gateway header dropped
    whole is not one of them: the gateway caller increments
    `METADATA_DROPPED.labels(path="gateway", reason="header")` itself.
    """
    for reason, number in asdict(drops).items():
        if number:
            METADATA_DROPPED.labels(path=path, reason=reason).inc(number)

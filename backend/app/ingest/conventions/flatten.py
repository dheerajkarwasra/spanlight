"""Unflattening indexed attributes into message objects (spec §4.3, "Unflattening").

OpenInference and Traceloop export messages as `prefix.<index>.<rest>` keys. `unflatten` groups
them by index in numeric order (`01` and `1` are the same message) and builds one object per
index from `<rest>` split on `.`; a nested index segment becomes a nested list the same way. The
wrapper segments the conventions put after an index (`message`, `message_content`, `tool_call`,
`prompt`, `completion`) are dropped so a message reads `{"role": "user", "content": "…"}`.
Values stay as sent: `content` holding JSON text is not parsed.

Limits are constants, not settings: at most `MAX_INDICES` messages and `MAX_DEPTH` list levels
per prefix. A key past a limit, or one that collides with an earlier key, is dropped and counted.
Dropped keys are still consumed: an unreadable fragment is not worth a stored-attribute slot.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

__all__ = ["MAX_DEPTH", "MAX_INDICES", "Unflattened", "unflatten"]

MAX_INDICES = 256
MAX_DEPTH = 4

# Leading zeros do not count against the four digits: `00012` is index 12, `12345` is no index.
_INDEX = re.compile(r"0*[0-9]{1,4}")
_WRAPPERS = frozenset({"message", "message_content", "tool_call", "prompt", "completion"})


@dataclass(frozen=True)
class Unflattened:
    items: tuple[dict[str, Any], ...]
    # Keys read from the attributes, to be removed from the stored ones.
    consumed: frozenset[str]
    # Keys past the limits or colliding with an earlier key.
    dropped: int


class _Node(dict[str | int, Any]):
    """A container built here, as opposed to a dict value the exporter sent."""


def unflatten(attributes: Mapping[str, Any], prefix: str) -> Unflattened:
    head = prefix + "."
    groups: dict[int, list[tuple[tuple[str | int, ...], Any]]] = {}
    consumed: set[str] = set()
    dropped = 0
    for key, value in attributes.items():
        if not key.startswith(head):
            continue
        index, _, rest = key[len(head) :].partition(".")
        if not _INDEX.fullmatch(index) or not rest:
            continue
        consumed.add(key)
        path = _path(rest)
        if path is None:
            dropped += 1
            continue
        groups.setdefault(int(index), []).append((path, value))
    kept = sorted(groups)[:MAX_INDICES]
    dropped += len(groups) - len(kept)
    items: list[dict[str, Any]] = []
    for number in kept:
        node = _Node()
        for path, value in groups[number]:
            if not _set(node, path, value):
                dropped += 1
        items.append({str(k): _materialise(v) for k, v in node.items()})
    return Unflattened(tuple(items), frozenset(consumed), dropped)


# `<rest>` repeats across messages (`message.role`, `message.content`), so parse it once.
@lru_cache(maxsize=1024)
def _path(rest: str) -> tuple[str | int, ...] | None:
    """Segments of `<rest>` with index segments as ints and wrapper segments dropped.

    `None` when the key nests deeper than `MAX_DEPTH` lists (the top index is level one) or
    names nothing once the wrapper is gone.
    """
    path: list[str | int] = []
    after_index = True
    for segment in rest.split("."):
        if _INDEX.fullmatch(segment):
            path.append(int(segment))
            after_index = True
        elif after_index and segment in _WRAPPERS:
            after_index = False
        else:
            path.append(segment)
            after_index = False
    if not path or 1 + sum(isinstance(s, int) for s in path) > MAX_DEPTH:
        return None
    return tuple(path)


def _set(node: _Node, path: tuple[str | int, ...], value: Any) -> bool:
    """Store `value` at `path`; `False` when an earlier key already holds that place."""
    for segment in path[:-1]:
        child: Any = node.setdefault(segment, _Node())
        if not isinstance(child, _Node):
            return False
        node = child
    if path[-1] in node:
        return False
    node[path[-1]] = value
    return True


def _materialise(value: Any) -> Any:
    if not isinstance(value, _Node):
        return value
    if all(isinstance(k, int) for k in value):
        return [_materialise(value[k]) for k in sorted(value)]
    return {str(k): _materialise(v) for k, v in value.items()}

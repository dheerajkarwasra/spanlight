"""Trace-level state shared by every span of one trace.

The server rejects every span of a trace whose trace-level fields break its limits, so the
SDK makes them fit instead: ``""`` means unset, a non-string becomes its text, text is cut to
the limit (name, user and session ids 256 characters, environment 64, release 128) and a trace
keeps at most 20 tags of up to 64 characters. Each kind of fix is logged once per process.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable, Mapping

from ._ids import new_trace_id
from ._metadata import clean_metadata, merge_metadata
from ._serialize import JsonValue

logger = logging.getLogger("spanlight")

MAX_TEXT = 256
MAX_ENVIRONMENT = 64
MAX_RELEASE = 128
MAX_TAGS = 20
MAX_TAG = 64

_logged: set[str] = set()


def _log_once(key: str, message: str, *args: object) -> None:
    if key not in _logged:
        _logged.add(key)
        logger.warning("Spanlight: " + message + " (logged once)", *args)


def clean_text(field: str, value: object, limit: int) -> str | None:
    """``value`` as text of 1 to ``limit`` characters, as the server accepts it, or ``None``."""
    if value is None:
        return None
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:
        _log_once(field + ".text", "trace %s cannot be turned into text; leaving it unset", field)
        return None
    if not text:
        return None
    if len(text) > limit:
        _log_once(field + ".long", "trace %s over %d characters; cutting it", field, limit)
        return text[:limit]
    return text


class TraceState:
    """Mutable trace-level fields shared by every span of one trace.

    Instances are shared by reference between parent and child spans
    (including children running in other asyncio tasks), so an
    :func:`~spanlight.update_trace` call anywhere in the trace is
    visible to every span exported afterwards.
    """

    def __init__(
        self,
        *,
        trace_id: str | None = None,
        name: str | None = None,
        environment: str | None = None,
        release: str | None = None,
    ) -> None:
        self.trace_id = trace_id or new_trace_id()
        self.name = clean_text("name", name, MAX_TEXT)
        self.environment = clean_text("environment", environment, MAX_ENVIRONMENT)
        self.release = clean_text("release", release, MAX_RELEASE)
        self.user_id: str | None = None
        self.session_id: str | None = None
        self.tags: list[str] = []
        self.metadata: dict[str, str] = {}
        self._lock = threading.Lock()

    def update(
        self,
        *,
        name: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        tags: Iterable[str] | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        """Set trace-level fields. ``None`` or ``""`` leaves a field unchanged.

        Tags accumulate; a single string is one tag. Metadata accumulates per key, a later value
        replacing an earlier one; invalid entries are dropped (see :mod:`spanlight._metadata`).
        """
        cleaned = clean_metadata(metadata)
        name = clean_text("name", name, MAX_TEXT)
        user_id = clean_text("user_id", user_id, MAX_TEXT)
        session_id = clean_text("session_id", session_id, MAX_TEXT)
        single = isinstance(tags, str) or not isinstance(tags, Iterable | None)
        new_tags = [clean_text("tag", tag, MAX_TAG) for tag in ([tags] if single else tags or ())]
        with self._lock:
            if name is not None:
                self.name = name
            if user_id is not None:
                self.user_id = user_id
            if session_id is not None:
                self.session_id = session_id
            for tag in new_tags:
                if tag is None or tag in self.tags:
                    continue
                if len(self.tags) >= MAX_TAGS:
                    _log_once("tags", "a trace keeps at most %d tags; dropping the rest", MAX_TAGS)
                    break
                self.tags.append(tag)
            if cleaned:
                self.metadata = merge_metadata(self.metadata, cleaned)

    def to_payload(self) -> dict[str, JsonValue]:
        """Return the ``trace`` object of the ingestion payload."""
        with self._lock:
            payload: dict[str, JsonValue] = {
                "name": self.name,
                "environment": self.environment,
                "release": self.release,
                "user_id": self.user_id,
                "session_id": self.session_id,
                "tags": list(self.tags),
            }
            # Only when set: a server older than trace metadata ignores the field anyway.
            if self.metadata:
                payload["metadata"] = dict(self.metadata)
            return payload

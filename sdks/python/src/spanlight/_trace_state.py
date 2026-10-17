"""Trace-level state shared by every span of one trace."""

from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping

from ._ids import new_trace_id
from ._metadata import clean_metadata, merge_metadata
from ._serialize import JsonValue


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
        self.name = name
        self.environment = environment
        self.release = release
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
        """Set trace-level fields. ``None`` leaves a field unchanged.

        Tags accumulate. Metadata accumulates per key, a later value replacing an earlier one;
        invalid entries are dropped (see :mod:`spanlight._metadata`).
        """
        cleaned = clean_metadata(metadata)
        with self._lock:
            if name is not None:
                self.name = name
            if user_id is not None:
                self.user_id = user_id
            if session_id is not None:
                self.session_id = session_id
            if tags is not None:
                for tag in tags:
                    if tag not in self.tags:
                        self.tags.append(str(tag))
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

"""The in-process loss counter: losses by (project, source, reason, hour), flushed periodically.

Incremented on hot paths (the gateway recorder, ingest), so `add` only touches a dictionary
behind a lock. A flush `drain`s the whole map at once and `restore`s the entries it could not
write, so a failed flush loses nothing.

The map holds at most `MAX_KEYS` distinct keys. Past that, a key not already present is counted
under `(project, source, "other", hour)` instead, which bounds memory however many distinct
reasons arrive; those `other` keys are themselves added past the cap, one per project, source
and hour at most.
"""

import threading
from collections.abc import Mapping
from datetime import datetime
from uuid import UUID

type LossKey = tuple[UUID, str, str, datetime]


class LossCounter:
    MAX_KEYS = 10_000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[LossKey, int] = {}

    def add(self, project_id: UUID, source: str, reason: str, hour: datetime, n: int = 1) -> None:
        """Count `n` lost spans; a count below one is ignored."""
        if n < 1:
            return
        with self._lock:
            self._add_locked((project_id, source, reason, hour), n)

    def drain(self) -> dict[LossKey, int]:
        """Every count so far, leaving the counter empty."""
        with self._lock:
            drained, self._counts = self._counts, {}
        return drained

    def restore(self, entries: Mapping[LossKey, int]) -> None:
        """Put drained entries back (a flush that failed), adding to what arrived meanwhile."""
        with self._lock:
            for key, n in entries.items():
                if n >= 1:
                    self._add_locked(key, n)

    def _add_locked(self, key: LossKey, n: int) -> None:
        if key not in self._counts and len(self._counts) >= self.MAX_KEYS:
            project_id, source, _, hour = key
            key = (project_id, source, "other", hour)
        self._counts[key] = self._counts.get(key, 0) + n

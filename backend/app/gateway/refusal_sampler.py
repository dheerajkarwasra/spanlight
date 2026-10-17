"""Which key-limit refusals leave a span: at most one per gateway key per interval. Pure.

A refusal costs its caller only a 429, so without a bound one key sending far above its limit
would turn every refusal into a span write. `RefusalSampler.admit` lets the first refusal of a key
through and then suppresses the key's refusals for `interval_s`; the next refusal let through
carries how many were suppressed in between, so the stored spans plus their suppressed counts
add up to every refusal (except those still waiting for the key's next span).

Memory is bounded: at most `max_keys` keys are tracked. When the map is full, the key whose last
span is oldest is forgotten, with any count it was still holding, so the counts are a lower
bound. Thread-safe; no I/O.
"""

import threading
import uuid

DEFAULT_INTERVAL_S = 1.0
DEFAULT_MAX_KEYS = 10_000


class RefusalSampler:
    """Per-key sampling of refusal spans; see the module docstring."""

    def __init__(
        self, interval_s: float = DEFAULT_INTERVAL_S, max_keys: int = DEFAULT_MAX_KEYS
    ) -> None:
        self.interval_s = interval_s
        self.max_keys = max_keys
        self._lock = threading.Lock()
        # key -> (when its last span was let through, refusals suppressed since). Insertion order
        # is the order spans were let through: a key is moved to the end each time.
        self._keys: dict[uuid.UUID, tuple[float, int]] = {}

    def admit(self, key_id: uuid.UUID, now: float) -> int | None:
        """The number of refusals suppressed since the key's last span when this refusal gets a
        span, or None when it is suppressed. `now` is a monotonic time in seconds."""
        with self._lock:
            entry = self._keys.get(key_id)
            if entry is not None and now - entry[0] < self.interval_s:
                self._keys[key_id] = (entry[0], entry[1] + 1)
                return None
            suppressed = entry[1] if entry is not None else 0
            self._keys.pop(key_id, None)
            self._make_room()
            self._keys[key_id] = (now, 0)
            return suppressed

    def pending(self, key_id: uuid.UUID) -> int:
        """Refusals of the key suppressed since its last span (not yet carried by a span)."""
        with self._lock:
            entry = self._keys.get(key_id)
            return entry[1] if entry is not None else 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._keys)

    def _make_room(self) -> None:
        """Forget the keys whose last span is oldest until one more fits."""
        while len(self._keys) >= self.max_keys:
            self._keys.pop(next(iter(self._keys)))

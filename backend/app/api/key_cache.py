"""Per-process cache of project API key rows, keyed by the key's public prefix.

Every ingest batch and every key read used to look its key up by prefix. A hit here skips that
round trip and the ORM row; it does not skip the checks. The presented secret is compared with
the cached hash on every request, and `revoked_at` and `expires_at` are judged from the cached
row, so a wrong secret, a revoked key and an expired one are refused exactly as on a miss.

What a hit can get wrong is only what changed in the database after the row was read: a key
revoked through another api process keeps working on this one for at most the TTL. A revocation
on this process evicts the key at once (`app.api.v1.keys`), and so does deleting a project or an
organization. Only rows that exist are cached, so unknown prefixes always reach the database and
cannot fill the map. Sessions and personal access tokens are not cached.
"""

import time
from dataclasses import dataclass, replace
from datetime import datetime

from app.api.principals import ApiKeyPrincipal


@dataclass(frozen=True)
class CachedKey:
    principal: ApiKeyPrincipal
    secret_hash: bytes
    revoked_at: datetime | None
    expires_at: datetime | None
    last_used_at: datetime | None


class KeyCache:
    """Bounded, TTL'd map from prefix to `CachedKey`. `ttl_s` 0 turns it off.

    Not thread-safe; it is used from the event loop only, and no method awaits.
    """

    def __init__(self, ttl_s: float, max_entries: int = 10_000) -> None:
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        # Insertion order is age order (`put` re-inserts), so the oldest entries are first.
        self._entries: dict[str, tuple[float, CachedKey]] = {}

    def get(self, prefix: str) -> CachedKey | None:
        entry = self._entries.get(prefix)
        if entry is None:
            return None
        stale_at, key = entry
        if time.monotonic() >= stale_at:
            del self._entries[prefix]
            return None
        return key

    def put(self, prefix: str, key: CachedKey) -> None:
        if self._ttl_s <= 0:
            return
        now = time.monotonic()
        self._entries.pop(prefix, None)
        while self._entries:
            oldest = next(iter(self._entries))
            if len(self._entries) < self._max_entries and self._entries[oldest][0] > now:
                break
            del self._entries[oldest]
        self._entries[prefix] = (now + self._ttl_s, key)

    def touch(self, prefix: str, last_used_at: datetime) -> None:
        """Record a `last_used_at` write, so the once-a-minute skip keeps working on hits."""
        entry = self._entries.get(prefix)
        if entry is not None:
            self._entries[prefix] = (entry[0], replace(entry[1], last_used_at=last_used_at))

    def evict(self, prefix: str) -> None:
        self._entries.pop(prefix, None)

    def clear(self) -> None:
        self._entries.clear()

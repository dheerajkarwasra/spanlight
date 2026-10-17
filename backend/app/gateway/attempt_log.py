"""The attempt log of a gateway span (`spanlight.gateway.attempt_log`). Pure.

One entry per upstream attempt, in order: the route target's index, its credential's name, the
provider status (None when no response came), the attempt's error kind, how long it took and the
provider's `Retry-After`. A route allows up to 40 attempts (8 targets x 5), so a long call keeps
its first 9 attempts and its last one; `spanlight.gateway.attempts` still holds the true count.
An entry holds nothing an `Attempt` record does not: no headers, bodies or key material.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from app.gateway.context import Attempt

MAX_ENTRIES = 10


def build(attempts: Sequence[Attempt], names: Mapping[int, str | None]) -> list[dict[str, Any]]:
    """The log of `attempts`; `names` maps a target index to its credential's name."""
    kept = (
        attempts if len(attempts) <= MAX_ENTRIES else [*attempts[: MAX_ENTRIES - 1], attempts[-1]]
    )
    return [_entry(attempt, names.get(attempt.target_index)) for attempt in kept]


def _entry(attempt: Attempt, name: str | None) -> dict[str, Any]:
    return {
        "target": attempt.target_index,
        "name": name,
        "status": attempt.status,
        "error": attempt.error,
        "duration_ms": attempt.duration_ms,
        "retry_after_s": attempt.retry_after,
    }

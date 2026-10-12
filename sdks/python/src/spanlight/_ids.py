"""W3C Trace Context compatible identifier generation.

Trace ids are 16 random bytes rendered as 32 lowercase hex characters and span
ids are 8 random bytes rendered as 16 lowercase hex characters. The all-zero
value is invalid in W3C Trace Context, so it is never returned.

Record ids the server deduplicates on (scores) are UUIDv7: time-ordered, so
they index well, and random enough that two clients never collide.
"""

from __future__ import annotations

import secrets
import time
import uuid

_TRACE_ID_BYTES = 16
_SPAN_ID_BYTES = 8


def _random_hex(num_bytes: int) -> str:
    while True:
        value = secrets.token_hex(num_bytes)
        if value.strip("0"):
            return value


def new_trace_id() -> str:
    """Return a new random 32-hex-character trace id."""
    return _random_hex(_TRACE_ID_BYTES)


def new_span_id() -> str:
    """Return a new random 16-hex-character span id."""
    return _random_hex(_SPAN_ID_BYTES)


def new_uuid7() -> str:
    """Return a new UUIDv7 (RFC 9562): 48-bit Unix milliseconds, then 74 random bits."""
    millis = time.time_ns() // 1_000_000
    random_bits = int.from_bytes(secrets.token_bytes(10), "big")
    rand_a = random_bits >> 68  # 12 bits
    rand_b = random_bits & ((1 << 62) - 1)  # 62 bits
    value = (millis & ((1 << 48) - 1)) << 80 | 0x7 << 76 | rand_a << 64 | 0b10 << 62 | rand_b
    return str(uuid.UUID(int=value))

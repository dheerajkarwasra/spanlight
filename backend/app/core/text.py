"""Text that PostgreSQL will store. Pure: no database, no I/O.

PostgreSQL's `text` and `jsonb` refuse the NUL character, and a lone UTF-16 surrogate (which a
JSON `\\ud800` escape can produce) cannot be encoded as UTF-8. Either one in a batch insert fails
the whole statement, so text from clients, providers and webhooks passes through here first.
"""

from typing import Any

NUL = "\x00"


def clean_text(value: str) -> str:
    """`value` without NUL, with each lone surrogate replaced by `?`."""
    without_nul = value.replace(NUL, "")
    try:
        without_nul.encode("utf-8")
    except UnicodeEncodeError:
        return without_nul.encode("utf-8", "replace").decode("utf-8")
    return without_nul


def without_nul(value: Any) -> Any:
    """A JSON value with NUL removed from every string and key."""
    if isinstance(value, str):
        return value.replace(NUL, "")
    if isinstance(value, list):
        return [without_nul(item) for item in value]
    if isinstance(value, dict):
        return {without_nul(key): without_nul(item) for key, item in value.items()}
    return value

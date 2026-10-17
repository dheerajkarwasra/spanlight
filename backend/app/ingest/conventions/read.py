"""Attribute reads shared by every convention mapper.

Mappers call `first(attributes, "literal.key", ...)` and `key.startswith("literal.prefix")` so the
docs-site drift guard sees every attribute name Spanlight reads. The value rules are Bet 1's
(`otlp_usage`), wrapped here rather than copied so there is one "first value" semantic.
"""

from collections.abc import Mapping
from typing import Any

from app.ingest.otlp_usage import as_text, first_value

__all__ = ["as_text", "first", "has_prefix"]


def first(attributes: Mapping[str, Any], *keys: str) -> Any:
    """The value of the first key present and not an empty string."""
    return first_value(attributes, keys)


def has_prefix(attributes: Mapping[str, Any], prefix: str) -> bool:
    return any(key.startswith(prefix) for key in attributes)

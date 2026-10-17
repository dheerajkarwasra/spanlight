"""Token totals by billing class for one reconciliation group, on each side. Pure: no I/O.

Only `tokens` lines count on the provider side: `other`, `code_execution`, credits and the
§7.4 priority usage lines never enter the comparison. A provider count is `None` when any of
its lines has no quantity, never 0.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.reconciliation.types import CostRollupRow, MappedLine, TokenType

__all__ = ["TokenTotals", "provider_tokens", "spanlight_tokens"]

# Provider token types onto the five comparison fields; OpenAI's `input` is its uncached
# input and `cached_input` its cache reads (as in `usage_merge`).
_TOKEN_FIELD: dict[TokenType, str] = {
    "uncached_input": "uncached_input",
    "input": "uncached_input",
    "cache_read": "cache_read",
    "cached_input": "cache_read",
    "cache_write_5m": "cache_write_5m",
    "cache_write_1h": "cache_write_1h",
    "output": "output",
}
_FIELDS = ("uncached_input", "cache_read", "cache_write_5m", "cache_write_1h", "output")


@dataclass(frozen=True)
class TokenTotals:
    """Token counts by billing class; a provider count is `None` when any line's is unknown."""

    uncached_input: int | None
    cache_read: int | None
    cache_write_5m: int | None
    cache_write_1h: int | None
    output: int | None


def provider_tokens(lines: Sequence[MappedLine]) -> TokenTotals:
    """Token totals of the `tokens` lines; a line with no token type belongs to no field."""
    totals: dict[str, int | None] = dict.fromkeys(_FIELDS, 0)
    for line in lines:
        if line.line_kind != "tokens" or line.token_type is None:
            continue
        name = _TOKEN_FIELD[line.token_type]
        seen = totals[name]
        totals[name] = None if seen is None or line.quantity is None else seen + line.quantity
    return TokenTotals(**totals)


def spanlight_tokens(rows: Sequence[CostRollupRow]) -> TokenTotals:
    """Token totals of the rollup rows. The rollup sums an unreported output count as 0, so the
    output total is `None` while any row has such a call: unknown is never a low figure."""
    unreported = any(row.usage_unreported_calls > 0 for row in rows)
    return TokenTotals(
        uncached_input=sum(row.uncached_input_tokens for row in rows),
        cache_read=sum(row.cache_read_tokens for row in rows),
        cache_write_5m=sum(row.cache_write_5m_tokens for row in rows),
        cache_write_1h=sum(row.cache_write_1h_tokens for row in rows),
        output=None if unreported else sum(row.output_tokens for row in rows),
    )

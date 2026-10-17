"""Usage-side joins: token counts onto cost lines, priority usage as lines (spec §7.4). Pure.

The cost reports carry USD but no counts; the usage reports carry counts but no USD. The two
join on an exact `(day, model, service_tier, context_window, provider_scope)` key, never a near
match, so a count only lands on the line it was billed under. Anthropic priority-tier usage is
not in the cost report at all (billed under a capacity commitment, §4.1): it becomes a synthetic
`other` line at `amount_usd 0` so the report can state the tokens without inventing a figure.
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal

from app.pricing.tiers import ServiceTier
from app.reconciliation.line_items import line_key, model_pattern_for
from app.reconciliation.types import MappedLine, TokenType, UsageBucketResult

PRIORITY_DESCRIPTION = "priority tier usage (not in cost report)"

# Which usage count fills a token line of each type. OpenAI's `input` is its uncached input and
# `cached input` its cache reads, so both providers read one table.
_COUNT_FIELD: dict[TokenType, str] = {
    "uncached_input": "uncached_input",
    "cache_read": "cache_read",
    "cache_write_5m": "cache_write_5m",
    "cache_write_1h": "cache_write_1h",
    "output": "output",
    "input": "uncached_input",
    "cached_input": "cache_read",
}
_TOKEN_FIELDS = ("uncached_input", "cache_read", "cache_write_5m", "cache_write_1h", "output")

UsageKey = tuple[date, str | None, ServiceTier | None, str | None, str | None]


def merge_usage_counts(
    lines: Sequence[MappedLine],
    usage: Sequence[UsageBucketResult],
    patterns: Sequence[str] = (),
) -> list[MappedLine]:
    """`lines` with `quantity` filled on token lines, then one synthetic line per priority key.

    A line that already carries a quantity keeps it. Usage results sharing a key are summed, so
    a report split across rows still joins. The input sequences are not modified.
    """
    totals = _sum_by_key(usage)
    merged = [_fill_quantity(line, totals) for line in lines]
    for key in sorted(totals, key=_sort_key):
        if key[2] == "priority":
            merged.append(_priority_line(totals[key], patterns))
    return merged


def _key_of(item: UsageBucketResult) -> UsageKey:
    return item.day, item.model, item.service_tier, item.context_window, item.provider_scope


def _key_of_line(line: MappedLine) -> UsageKey:
    return line.day, line.model_raw, line.service_tier, line.context_window, line.provider_scope


def _sort_key(key: UsageKey) -> tuple[date, str, str, str, str]:
    return key[0], key[1] or "", key[2] or "", key[3] or "", key[4] or ""


def _sum_by_key(usage: Sequence[UsageBucketResult]) -> dict[UsageKey, UsageBucketResult]:
    totals: dict[UsageKey, UsageBucketResult] = {}
    for item in usage:
        key = _key_of(item)
        seen = totals.get(key)
        totals[key] = item if seen is None else _add(seen, item)
    return totals


def _add(a: UsageBucketResult, b: UsageBucketResult) -> UsageBucketResult:
    return replace(
        a,
        uncached_input=_add_counts(a.uncached_input, b.uncached_input),
        cache_read=_add_counts(a.cache_read, b.cache_read),
        cache_write_5m=_add_counts(a.cache_write_5m, b.cache_write_5m),
        cache_write_1h=_add_counts(a.cache_write_1h, b.cache_write_1h),
        output=_add_counts(a.output, b.output),
        web_search_requests=_add_counts(a.web_search_requests, b.web_search_requests),
    )


def _add_counts(a: int | None, b: int | None) -> int | None:
    if a is None:
        return b
    if b is None:
        return a
    return a + b


def _fill_quantity(line: MappedLine, totals: Mapping[UsageKey, UsageBucketResult]) -> MappedLine:
    if line.line_kind != "tokens" or line.token_type is None or line.quantity is not None:
        return line
    usage = totals.get(_key_of_line(line))
    if usage is None:
        return line
    count: int | None = getattr(usage, _COUNT_FIELD[line.token_type])
    if count is None:
        return line
    return replace(line, quantity=count)


def _priority_line(usage: UsageBucketResult, patterns: Sequence[str]) -> MappedLine:
    counts = [count for field in _TOKEN_FIELDS if (count := getattr(usage, field)) is not None]
    tokens = sum(counts) if counts else None
    return MappedLine(
        day=usage.day,
        provider=usage.provider,
        line_key=line_key(
            usage.provider_scope,
            PRIORITY_DESCRIPTION,
            None,
            "priority",
            usage.context_window,
            usage.model,
            "other",
        ),
        line_kind="other",
        model_raw=usage.model,
        model_pattern=model_pattern_for(usage.model, patterns),
        token_type=None,
        service_tier="priority",
        context_window=usage.context_window,
        provider_scope=usage.provider_scope,
        description=PRIORITY_DESCRIPTION,
        quantity=tokens,
        amount_usd=Decimal(0),
    )

"""Usage-side joins: token counts onto cost lines, priority usage as lines (spec §7.4). Pure.

Failure mode: a count lands on the wrong line (a near key, another workspace, another tier),
so the report shows tokens that were never billed under that line; or Anthropic priority usage,
which the cost report omits, vanishes instead of showing as commitment-billed tokens.

The usage pages in `fixtures/` are written by hand from spec §4.2-4.3 (two buckets, `has_more`).
"""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.reconciliation.anthropic_lines import (
    map_anthropic_cost_result,
    map_anthropic_usage_result,
)
from app.reconciliation.line_items import line_key
from app.reconciliation.openai_lines import map_openai_cost_result, map_openai_usage_result
from app.reconciliation.types import MappedLine, UsageBucketResult
from app.reconciliation.usage_merge import PRIORITY_DESCRIPTION, merge_usage_counts

FIXTURES = Path(__file__).parent / "fixtures"
DAY = date(2026, 10, 1)
PATTERNS = ["claude-sonnet-4-5", "claude-opus-4-1", "gpt-4o"]


def load_page(name: str) -> dict[str, Any]:
    page: dict[str, Any] = json.loads((FIXTURES / name).read_text())
    return page


def anthropic_cost_lines(scope: str | None = "default") -> list[MappedLine]:
    lines: list[MappedLine] = []
    for bucket in load_page("anthropic_cost_page.json")["data"]:
        day = date.fromisoformat(bucket["starting_at"][:10])
        for result in bucket["results"]:
            line = map_anthropic_cost_result(day, result, scope, PATTERNS)
            if line is not None:
                lines.append(line)
    return lines


def anthropic_usage(scope: str | None = "default") -> list[UsageBucketResult]:
    usage: list[UsageBucketResult] = []
    for bucket in load_page("anthropic_usage_page.json")["data"]:
        day = date.fromisoformat(bucket["starting_at"][:10])
        for result in bucket["results"]:
            mapped = map_anthropic_usage_result(day, result, scope)
            if mapped is not None:
                usage.append(mapped)
    return usage


def openai_cost_lines(scope: str | None = "proj_abc123") -> list[MappedLine]:
    lines: list[MappedLine] = []
    for bucket in load_page("openai_costs_page.json")["data"]:
        day = datetime.fromtimestamp(bucket["start_time"], tz=UTC).date()
        for result in bucket["results"]:
            line = map_openai_cost_result(day, result, scope, PATTERNS)
            if line is not None:
                lines.append(line)
    return lines


def openai_usage(scope: str | None = "proj_abc123") -> list[UsageBucketResult]:
    usage: list[UsageBucketResult] = []
    for bucket in load_page("openai_usage_page.json")["data"]:
        day = datetime.fromtimestamp(bucket["start_time"], tz=UTC).date()
        for result in bucket["results"]:
            mapped = map_openai_usage_result(day, result, scope)
            if mapped is not None:
                usage.append(mapped)
    return usage


def sonnet_usage(**overrides: Any) -> UsageBucketResult:
    fields: dict[str, Any] = {
        "day": DAY,
        "provider": "anthropic",
        "model": "claude-sonnet-4-5-20250929",
        "service_tier": "standard",
        "context_window": "0-200k",
        "provider_scope": None,
        "uncached_input": 41150,
        "cache_read": 21667,
        "cache_write_5m": 3333,
        "cache_write_1h": 667,
        "output": 16667,
        "web_search_requests": 1000,
    }
    fields.update(overrides)
    return UsageBucketResult(**fields)


def by_key(lines: list[MappedLine]) -> dict[tuple[date, str], MappedLine]:
    return {(line.day, line.line_key): line for line in lines}


# --- usage page mapping ---------------------------------------------------------------------------


def test_anthropic_usage_fixture_maps_every_result() -> None:
    usage = anthropic_usage(scope=None)

    assert len(usage) == 4
    assert usage[0] == sonnet_usage()
    assert usage[1].service_tier == "priority"
    assert usage[1].model == "claude-opus-4-1-20250805"
    assert usage[2].provider_scope == "wrkspc_01ABCDEFGHIJKLMNOPQRSTUV"
    assert usage[2].service_tier == "batch"
    assert usage[3].day == date(2026, 10, 2)
    assert all(item.provider == "anthropic" for item in usage)


def test_anthropic_usage_workspace_filter() -> None:
    assert len(anthropic_usage(scope="default")) == 3
    assert len(anthropic_usage(scope="wrkspc_01ABCDEFGHIJKLMNOPQRSTUV")) == 1
    assert anthropic_usage(scope="wrkspc_01XYZ") == []


def test_anthropic_usage_missing_counts_are_none() -> None:
    result = {"model": "claude-sonnet-4-5-20250929", "service_tier": "priority_on_demand"}

    mapped = map_anthropic_usage_result(DAY, result, None)

    assert mapped is not None
    assert mapped.service_tier == "priority"
    assert mapped.uncached_input is None
    assert mapped.cache_write_5m is None
    assert mapped.web_search_requests is None
    assert mapped.context_window is None


def test_openai_usage_fixture_maps_every_result() -> None:
    usage = openai_usage()

    assert len(usage) == 3
    assert usage[0] == UsageBucketResult(
        day=DAY,
        provider="openai",
        model="gpt-4o-2024-08-06",
        service_tier=None,
        context_window=None,
        provider_scope="proj_abc123",
        uncached_input=40000,
        cache_read=5000,
        cache_write_5m=None,
        cache_write_1h=None,
        output=25000,
        web_search_requests=None,
    )
    assert usage[1].service_tier == "batch"
    assert usage[1].model == "gpt-4o"
    assert usage[2].day == date(2026, 10, 2)
    assert len(openai_usage(scope=None)) == 4


# --- merge ----------------------------------------------------------------------------------------


def test_merge_fills_quantity_on_exact_key_only() -> None:
    lines = anthropic_cost_lines()
    usage = [
        sonnet_usage(),
        # Near misses: another day, another workspace, another tier, another context window.
        sonnet_usage(day=date(2026, 10, 3), uncached_input=1),
        sonnet_usage(provider_scope="wrkspc_01ABCDEFGHIJKLMNOPQRSTUV", uncached_input=2),
        sonnet_usage(service_tier=None, uncached_input=3),
        sonnet_usage(context_window="200k-1M", uncached_input=4),
    ]

    merged = merge_usage_counts(lines, usage)

    assert len(merged) == len(lines)
    assert [line.line_key for line in merged] == [line.line_key for line in lines]
    day_one = [line for line in merged if line.day == DAY]
    filled = {line.token_type: line.quantity for line in day_one if line.line_kind == "tokens"}
    assert filled == {
        "uncached_input": 41150,
        "cache_read": 21667,
        "cache_write_5m": 3333,
        "cache_write_1h": 667,
        "output": 16667,
    }
    # Non-token lines and lines without a usage match stay unknown.
    assert all(line.quantity is None for line in day_one if line.line_kind != "tokens")
    assert all(line.quantity is None for line in merged if line.day != DAY)
    # The fixture's cost lines are untouched.
    assert all(line.quantity is None for line in lines)


def test_merge_openai_input_and_cached_input() -> None:
    lines = openai_cost_lines()

    merged = merge_usage_counts(lines, openai_usage(), PATTERNS)

    day_one = [line for line in merged if line.day == DAY and line.line_kind == "tokens"]
    quantities = {(line.description, line.service_tier): line.quantity for line in day_one}
    assert quantities["gpt-4o-2024-08-06, input", None] == 40000
    assert quantities["gpt-4o-2024-08-06, cached input", None] == 5000
    assert quantities["gpt-4o-2024-08-06, output", None] == 25000
    assert quantities["batch | gpt-4o, input", "batch"] == 32000
    # A fine-tuned model and an evals model have no usage row with that exact model string.
    assert quantities["ft-gpt-4o-2024-08-06, output", None] is None
    assert quantities["evals | gpt-4o-mini-2024-07-18, input", None] is None
    # No OpenAI usage row has a priority tier, so no synthetic line appears.
    assert len(merged) == len(lines)


def test_merge_adds_priority_synthetic_lines() -> None:
    lines = anthropic_cost_lines()
    usage = anthropic_usage()

    merged = merge_usage_counts(lines, usage, PATTERNS)

    synthetic = [line for line in merged if line.description == PRIORITY_DESCRIPTION]
    assert len(synthetic) == 1
    assert merged[: len(lines)] == [
        line for line in merged if line.description != PRIORITY_DESCRIPTION
    ]
    line = synthetic[0]
    assert line == MappedLine(
        day=DAY,
        provider="anthropic",
        line_key=line_key(
            None,
            PRIORITY_DESCRIPTION,
            None,
            "priority",
            "0-200k",
            "claude-opus-4-1-20250805",
            "other",
        ),
        line_kind="other",
        model_raw="claude-opus-4-1-20250805",
        model_pattern="claude-opus-4-1",
        token_type=None,
        service_tier="priority",
        context_window="0-200k",
        provider_scope=None,
        description="priority tier usage (not in cost report)",
        quantity=5000 + 1200 + 1500,
        amount_usd=Decimal("0"),
    )


def test_merge_priority_with_no_counts_has_unknown_quantity() -> None:
    usage = [
        sonnet_usage(
            service_tier="priority",
            uncached_input=None,
            cache_read=None,
            cache_write_5m=None,
            cache_write_1h=None,
            output=None,
        )
    ]

    merged = merge_usage_counts([], usage)

    assert len(merged) == 1
    assert merged[0].quantity is None
    assert merged[0].amount_usd == Decimal("0")


def test_merge_sums_duplicate_usage_keys() -> None:
    lines = anthropic_cost_lines()
    usage = [sonnet_usage(), sonnet_usage(uncached_input=10, output=None)]
    priority = sonnet_usage(
        service_tier="priority",
        uncached_input=100,
        cache_read=0,
        cache_write_5m=0,
        cache_write_1h=0,
        output=50,
    )

    merged = merge_usage_counts(lines, [*usage, priority, priority], PATTERNS)

    filled = {
        line.token_type: line.quantity
        for line in merged
        if line.day == DAY and line.line_kind == "tokens"
    }
    assert filled["uncached_input"] == 41160
    assert filled["output"] == 16667
    synthetic = [line for line in merged if line.description == PRIORITY_DESCRIPTION]
    assert len(synthetic) == 1
    assert synthetic[0].quantity == 300


def test_merge_keeps_a_quantity_already_known() -> None:
    lines = anthropic_cost_lines()
    known = [line for line in lines if line.token_type == "output" and line.day == DAY]
    assert len(known) == 1
    lines = [replace(line, quantity=7) if line is known[0] else line for line in lines]

    merged = merge_usage_counts(lines, [sonnet_usage()])

    output = [line for line in merged if line.token_type == "output" and line.day == DAY]
    assert output[0].quantity == 7


def test_merge_without_usage_returns_the_lines_unchanged() -> None:
    lines = anthropic_cost_lines()

    assert merge_usage_counts(lines, []) == lines
    assert merge_usage_counts([], []) == []

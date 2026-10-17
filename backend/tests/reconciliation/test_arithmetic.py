"""Reconciliation arithmetic (spec §7.5). Pure: no database, no I/O.

Failure mode: the report claims a day reconciles when it does not. A residual folded into an
explained line, a credit subtracted from the provider figure, an unmapped model merged into a
neighbour, or a day called settled before the provider's data lag is over each hide a real gap
between what Spanlight recorded and what the provider billed.
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.reconciliation.arithmetic import (
    NO_PROVIDER_LINE_NOTE,
    DayReconciliation,
    ExplainedLine,
    GroupReconciliation,
    ListedCharge,
    TokenTotals,
    group_key,
    is_provisional,
    reconcile_day,
)
from app.reconciliation.types import CostRollupRow, MappedLine
from app.reconciliation.usage_merge import PRIORITY_DESCRIPTION

# The 2026-10-16 snapshot is the first that marks Anthropic priority as commitment-billed.
DAY = date(2026, 10, 16)
DAY_END = datetime(2026, 10, 17, tzinfo=UTC)
SETTLED_NOW = DAY_END + timedelta(days=5)
PROJECT = UUID("00000000-0000-0000-0000-0000000000a1")


def line(amount: str, **overrides: Any) -> MappedLine:
    fields: dict[str, Any] = {
        "day": DAY,
        "provider": "anthropic",
        "line_key": uuid4().hex * 2,
        "line_kind": "tokens",
        "model_raw": "claude-sonnet-4-6",
        "model_pattern": "claude-sonnet-4-6",
        "token_type": "output",
        "service_tier": "standard",
        "context_window": None,
        "provider_scope": None,
        "description": "Claude Sonnet 4.6 output",
        "quantity": None,
        "amount_usd": Decimal(amount),
    }
    fields.update(overrides)
    return MappedLine(**fields)


def row(cost: str | None, **overrides: Any) -> CostRollupRow:
    fields: dict[str, Any] = {
        "project_id": PROJECT,
        "day": DAY,
        "provider": "anthropic",
        "provider_canonical": "anthropic",
        "model": "claude-sonnet-4-6",
        "model_pattern": "claude-sonnet-4-6",
        "service_tier": None,
        "calls": 1,
        "priced_calls": 1 if cost is not None else 0,
        "unpriced_calls": 0,
        "usage_unreported_calls": 0,
        "cut_stream_calls": 0,
        "cache_hit_calls": 0,
        "unmodelled_modifier_calls": 0,
        "uncached_input_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_5m_tokens": 0,
        "cache_write_1h_tokens": 0,
        "output_tokens": 0,
        "web_search_requests": 0,
        "cost_usd": None if cost is None else Decimal(cost),
        "fee_usd": None,
    }
    fields.update(overrides)
    return CostRollupRow(**fields)


def reconcile(
    lines: Sequence[MappedLine],
    rows: Sequence[CostRollupRow],
    *,
    provider: str = "anthropic",
    now: datetime = SETTLED_NOW,
    settled_fetch: bool = True,
    retention_cutoffs: dict[UUID, date] | None = None,
    day: date = DAY,
) -> DayReconciliation:
    return reconcile_day(
        day,
        provider,
        lines,
        rows,
        now=now,
        settled_fetch=settled_fetch,
        retention_cutoffs=retention_cutoffs or {},
    )


def only_group(result: DayReconciliation) -> GroupReconciliation:
    assert len(result.groups) == 1
    return result.groups[0]


def explained(group: GroupReconciliation, kind: str) -> ExplainedLine:
    found = [item for item in group.explained if item.kind == kind]
    assert len(found) == 1, f"expected one {kind} line, got {group.explained}"
    return found[0]


def kinds(group: GroupReconciliation) -> list[str]:
    return [item.kind for item in group.explained]


def test_residual_is_signed_provider_minus_spanlight_minus_explained() -> None:
    under = only_group(reconcile([line("10.00")], [row("9.50")]))
    assert under.provider_usd == Decimal("10.00")
    assert under.spanlight_usd == Decimal("9.50")
    assert under.explained_usd == Decimal(0)
    assert under.residual_usd == Decimal("0.50")
    assert under.variance_ratio == Decimal("0.0500")

    over = only_group(reconcile([line("10.00")], [row("10.40")]))
    assert over.residual_usd == Decimal("-0.40")
    assert over.variance_ratio == Decimal("-0.0400")
    assert over.explained == ()


def test_variance_ratio_is_quantized_to_four_decimals() -> None:
    group = only_group(reconcile([line("3.00000000")], [row("2.00000000")]))
    assert group.variance_ratio == Decimal("0.3333")
    assert group.variance_ratio.as_tuple().exponent == -4


def test_batch_spend_only_for_untraced_batch() -> None:
    standard = line("6.00")
    batch = line("4.00", service_tier="batch", description="Batch output")

    traced = only_group(
        reconcile([standard, batch], [row("6.00"), row("4.00", service_tier="batch")])
    )
    assert "batch_spend" not in kinds(traced)
    assert traced.residual_usd == Decimal(0)

    untraced = only_group(reconcile([standard, batch], [row("6.00")]))
    item = explained(untraced, "batch_spend")
    assert item.usd == Decimal("4.00")
    assert untraced.explained_usd == Decimal("4.00")
    assert untraced.residual_usd == Decimal(0)

    partial = only_group(
        reconcile([standard, batch], [row("6.00"), row("1.50", service_tier="batch")])
    )
    assert explained(partial, "batch_spend").usd == Decimal("2.50")
    assert partial.residual_usd == Decimal(0)

    # Spanlight over on batch is not explained by untraced batch: it stays in the residual.
    over = only_group(
        reconcile([standard, batch], [row("6.00"), row("5.00", service_tier="batch")])
    )
    assert "batch_spend" not in kinds(over)
    assert over.residual_usd == Decimal("-1.00")


def test_server_tool_fees_line() -> None:
    tokens = line("1.00")
    searches = line(
        "0.10",
        line_kind="web_search",
        token_type=None,
        description="Web search",
        quantity=10,
    )
    group = only_group(reconcile([tokens, searches], [row("1.00", web_search_requests=0)]))
    assert group.provider_usd == Decimal("1.10")
    item = explained(group, "server_tool_fees")
    assert item.usd == Decimal("0.10")
    assert item.count == 10
    assert item.tokens is None
    assert item.note == "10 web searches on the provider side, 0 recorded"
    assert group.residual_usd == Decimal(0)

    recorded = row("1.10", web_search_requests=10, fee_usd=Decimal("0.10"))
    matched = only_group(reconcile([tokens, searches], [recorded]))
    assert "server_tool_fees" not in kinds(matched)
    assert matched.residual_usd == Decimal(0)


def test_unpriced_spans_count() -> None:
    # An unpriced model has no pattern on either side, so both meet under its exact name.
    billed = line("1.50", model_raw="claude-x-preview", model_pattern=None)
    unpriced = row(
        None,
        model="claude-x-preview",
        model_pattern=None,
        calls=3,
        unpriced_calls=3,
        uncached_input_tokens=100,
        output_tokens=50,
    )
    group = only_group(reconcile([billed], [unpriced]))
    item = explained(group, "unpriced_spans")
    assert item.usd is None
    assert item.count == 3
    assert item.tokens == 150
    assert item.note == "3 calls with no price for their model: cost unknown"
    assert group.spanlight_usd is None
    assert group.explained_usd == Decimal(0)
    assert group.residual_usd == Decimal("1.50")


def test_unpriced_spans_tokens_unknown_when_row_mixes_priced_calls() -> None:
    mixed = row("1.00", calls=3, priced_calls=2, unpriced_calls=1, output_tokens=90)
    item = explained(only_group(reconcile([line("1.50")], [mixed])), "unpriced_spans")
    assert item.count == 1
    assert item.tokens is None


def test_usage_not_reported_with_cut_subset_note() -> None:
    rows = [
        row("1.00", calls=4, priced_calls=1, usage_unreported_calls=3, cut_stream_calls=2),
    ]
    group = only_group(reconcile([line("1.20")], rows))
    item = explained(group, "usage_not_reported")
    assert item.usd is None
    assert item.count == 3
    assert item.tokens is None
    assert item.note == "3 calls whose output tokens were never reported (2 cut streams)"
    assert group.residual_usd == Decimal("0.20")


def test_untraced_traffic_group() -> None:
    traced = line("1.00")
    opus = line(
        "2.00", model_raw="claude-opus-4-8", model_pattern="claude-opus-4-8", service_tier="batch"
    )
    opus_search = line(
        "0.05",
        model_raw="claude-opus-4-8",
        model_pattern="claude-opus-4-8",
        line_kind="web_search",
        token_type=None,
        quantity=5,
    )
    result = reconcile([traced, opus, opus_search], [row("1.00")])
    groups = {group.group: group for group in result.groups}
    untraced = groups["claude-opus-4-8"]
    assert untraced.spanlight_usd is None
    assert untraced.spanlight_models == ()
    # One explanation for the whole group: the batch and fee lines would count it twice.
    assert kinds(untraced) == ["untraced_traffic"]
    item = untraced.explained[0]
    assert item.usd == Decimal("2.05")
    assert item.note == "no span with this model"
    assert untraced.residual_usd == Decimal(0)
    assert untraced.variance_ratio == Decimal("0.0000")
    assert result.residual_usd == Decimal(0)


def test_spanlight_only_group_has_zero_provider_and_null_variance() -> None:
    group = only_group(reconcile([], [row("0.75")]))
    assert group.provider_usd == Decimal(0)
    assert group.provider_models == ()
    assert group.residual_usd == Decimal("-0.75")
    assert group.variance_ratio is None


def test_priority_commitment_tokens_without_usd() -> None:
    priority = line(
        "0",
        line_kind="other",
        token_type=None,
        service_tier="priority",
        description=PRIORITY_DESCRIPTION,
        quantity=12_000,
    )
    result = reconcile([line("1.00"), priority], [row("1.00")])
    group = only_group(result)
    item = explained(group, "priority_commitment")
    assert item.usd is None
    assert item.count is None
    assert item.tokens == 12_000
    assert item.note == "12,000 priority tokens, commitment-billed"
    # The synthetic line is not a charge: no other line, no provider USD, no provider tokens.
    assert result.other == ()
    assert result.other_usd == Decimal(0)
    assert group.provider_usd == Decimal("1.00")
    assert group.residual_usd == Decimal(0)
    assert group.note is None


def priority_usage(quantity: int | None) -> MappedLine:
    return line(
        "0",
        line_kind="other",
        token_type=None,
        service_tier="priority",
        description=PRIORITY_DESCRIPTION,
        quantity=quantity,
    )


def test_priority_commitment_explains_standard_priced_priority_rows() -> None:
    rows = [
        row("2.00"),
        row("1.00", service_tier="priority", calls=2, priced_calls=2, fee_usd=Decimal("0.01")),
    ]
    group = only_group(reconcile([line("2.00"), priority_usage(12_000)], rows))
    item = explained(group, "priority_commitment")
    assert item.usd == Decimal("-0.99")
    assert item.count == 2
    assert item.tokens == 12_000
    assert item.note == (
        "2 priority calls priced at the standard rate ($0.9900); commitment-billed, "
        "not in the provider cost report (12,000 priority tokens)"
    )
    assert explained(group, "server_tool_fees").usd == Decimal("-0.01")
    assert group.spanlight_usd == Decimal("3.00")
    assert group.explained_usd == Decimal("-1.00")
    assert group.residual_usd == Decimal(0)


def test_priority_commitment_usd_null_when_provider_bills_priority() -> None:
    billed = line("1.00", service_tier="priority", description="Priority output")
    rows = [row("1.00", service_tier="priority")]
    group = only_group(reconcile([billed], rows))
    item = explained(group, "priority_commitment")
    assert item.usd is None
    assert item.count == 1
    assert group.residual_usd == Decimal(0)


def test_zero_priority_usage_alone_makes_no_group() -> None:
    result = reconcile([line("1.00"), priority_usage(0)], [row("1.00")])
    assert "priority_commitment" not in kinds(only_group(result))
    other_model = priority_usage(0)
    other_model = replace(other_model, model_raw="claude-opus-4-8", model_pattern="claude-opus-4-8")
    assert len(reconcile([line("1.00"), other_model], [row("1.00")]).groups) == 1


def test_spanlight_only_group_note() -> None:
    result = reconcile([line("1.00")], [row("1.00"), row("0.50", model="m", model_pattern="m")])
    groups = {group.group: group for group in result.groups}
    assert groups["m"].note == NO_PROVIDER_LINE_NOTE
    assert NO_PROVIDER_LINE_NOTE == (
        "no provider line: wrong source scope, or usage billed under another account"
    )
    assert groups["claude-sonnet-4-6"].note is None
    priority_only = reconcile([priority_usage(10)], [row("0.10")])
    assert only_group(priority_only).note is None


def test_batch_spend_does_not_subtract_batch_fees_twice() -> None:
    batch = line("5.00", service_tier="batch", description="Batch output")
    search = line("0.10", line_kind="web_search", token_type=None, quantity=10)
    traced = row("3.10", service_tier="batch", web_search_requests=10, fee_usd=Decimal("0.10"))
    group = only_group(reconcile([batch, search], [traced]))
    assert explained(group, "batch_spend").usd == Decimal("2.00")
    assert "server_tool_fees" not in kinds(group)
    assert group.residual_usd == Decimal(0)


def test_fee_of_unpriced_row_is_not_subtracted() -> None:
    search = line("0.10", line_kind="web_search", token_type=None, quantity=10)
    unpriced = row(None, unpriced_calls=1, web_search_requests=10, fee_usd=Decimal("0.10"))
    group = only_group(reconcile([line("1.00"), search], [unpriced]))
    assert explained(group, "server_tool_fees").usd == Decimal("0.10")
    assert group.residual_usd == Decimal("1.00")


def test_batch_spend_note_when_batch_calls_have_no_cost() -> None:
    batch = line("5.00", service_tier="batch")
    rows = [row("3.00", service_tier="batch", calls=3, priced_calls=2, usage_unreported_calls=1)]
    item = explained(only_group(reconcile([batch], rows)), "batch_spend")
    assert item.usd == Decimal("2.00")
    assert item.note == "batch-tier spend not matched by priced batch spans (some have no cost)"
    plain = explained(
        only_group(reconcile([batch], [row("3.00", service_tier="batch")])), "batch_spend"
    )
    assert plain.note == "batch-tier spend not covered by traced batch calls"


def test_untraced_traffic_has_no_usd_past_retention() -> None:
    cutoffs = {PROJECT: DAY + timedelta(days=1)}
    result = reconcile([line("10.00")], [], retention_cutoffs=cutoffs)
    group = only_group(result)
    item = explained(group, "untraced_traffic")
    assert item.usd is None
    assert item.note == "no span with this model; spans for this day are past retention"
    assert "beyond_retention" in kinds(group)
    assert result.incomplete is True
    assert result.residual_usd == Decimal("10.00")
    assert result.variance_ratio == Decimal("1.0000")
    assert result.max_abs_variance_ratio == Decimal("1.0000")


def test_batch_and_fee_gaps_have_no_usd_past_retention() -> None:
    """A traced group past one project's retention: that project's deleted batch spans and fees
    would be explained away as untraced batch spend or unrecorded fees."""
    cutoffs = {PROJECT: DAY + timedelta(days=1)}
    lines = [
        line("1.00", service_tier="batch", token_type="uncached_input"),
        line("0.04", line_kind="web_search", token_type=None, service_tier=None, quantity=4),
    ]
    rows = [row("0.30", service_tier="batch", fee_usd=Decimal("0.01"), web_search_requests=1)]
    result = reconcile(lines, rows, retention_cutoffs=cutoffs)
    group = only_group(result)
    batch, fees = explained(group, "batch_spend"), explained(group, "server_tool_fees")
    assert (batch.usd, fees.usd) == (None, None)
    assert batch.note == (
        "batch-tier spend not covered by traced batch calls; spans for this day are past retention"
    )
    assert fees.note == (
        "4 web searches on the provider side, 1 recorded; spans for this day are past retention"
    )
    assert group.explained_usd == Decimal(0)
    assert group.residual_usd == Decimal("1.04") - Decimal("0.30")
    # Within retention the same gaps carry their amounts.
    within = only_group(reconcile(lines, rows))
    assert explained(within, "batch_spend").usd == Decimal("0.71")
    assert explained(within, "server_tool_fees").usd == Decimal("0.03")


def test_rows_without_provider_are_counted_apart() -> None:
    unattributed = row("4.00", provider=None, provider_canonical=None, calls=3)
    result = reconcile([line("1.00")], [row("1.00"), unattributed])
    assert result.unattributed_calls == 3
    assert only_group(result).spanlight_usd == Decimal("1.00")
    assert reconcile([line("1.00")], [row("1.00")]).unattributed_calls == 0


def test_naive_now_is_rejected() -> None:
    naive = datetime(2026, 10, 20)
    with pytest.raises(ValueError, match="timezone-aware"):
        is_provisional(DAY, naive, settled_fetch=True)
    with pytest.raises(ValueError, match="timezone-aware"):
        reconcile([line("1.00")], [], now=naive)


def test_unmodelled_modifiers_count() -> None:
    rows = [
        row(
            "2.00",
            provider="openai",
            provider_canonical="openai",
            model="gpt-4o",
            model_pattern="gpt-4o",
            calls=5,
            priced_calls=5,
            service_tier="priority",
            unmodelled_modifier_calls=5,
        )
    ]
    lines = [line("3.00", provider="openai", model_raw="gpt-4o", model_pattern="gpt-4o")]
    group = only_group(reconcile(lines, rows, provider="openai"))
    item = explained(group, "unmodelled_modifiers")
    assert item.usd is None
    assert item.count == 5
    assert item.note == (
        "5 calls priced at the standard rate with a modifier Spanlight does not price "
        "(OpenAI priority, fast mode, data residency)"
    )
    assert group.residual_usd == Decimal("1.00")


def test_beyond_retention_marks_day_incomplete() -> None:
    other_project = uuid4()
    cutoffs = {PROJECT: DAY + timedelta(days=1), other_project: DAY - timedelta(days=30)}
    result = reconcile([line("1.00")], [row("0.20")], retention_cutoffs=cutoffs)
    assert result.incomplete is True
    item = explained(only_group(result), "beyond_retention")
    assert item.usd is None
    assert item.count == 1
    assert item.note == "spans for this day are past the retention of 1 mapped project"

    kept = reconcile([line("1.00")], [row("0.20")], retention_cutoffs={PROJECT: DAY})
    assert kept.incomplete is False
    assert "beyond_retention" not in kinds(only_group(kept))


def test_credits_listed_apart_and_excluded() -> None:
    credit = line(
        "-5.00",
        line_kind="credit",
        token_type=None,
        model_raw=None,
        model_pattern=None,
        description="Promotional credit",
    )
    result = reconcile([line("10.00"), credit], [row("9.00")])
    assert result.credits == (ListedCharge(description="Promotional credit", usd=Decimal("-5.00")),)
    assert result.credits_usd == Decimal("-5.00")
    assert result.provider_usd == Decimal("10.00")
    assert result.residual_usd == Decimal("1.00")
    assert result.variance_ratio == Decimal("0.1000")
    assert [group.group for group in result.groups] == ["claude-sonnet-4-6"]


def test_other_charges_excluded_from_tokens_comparison() -> None:
    tokens = line("1.00", quantity=1_000)
    code = line(
        "0.02",
        line_kind="code_execution",
        token_type=None,
        description="Code Execution Usage",
        quantity=7,
    )
    session = line(
        "0.03",
        line_kind="other",
        token_type=None,
        model_raw=None,
        model_pattern=None,
        description="Session usage",
        quantity=9,
    )
    result = reconcile([tokens, code, session], [row("1.00", output_tokens=1_000)])
    group = only_group(result)
    assert group.provider_usd == Decimal("1.00")
    assert group.provider_tokens == TokenTotals(
        uncached_input=0, cache_read=0, cache_write_5m=0, cache_write_1h=0, output=1_000
    )
    assert group.spanlight_tokens.output == 1_000
    assert result.other == (
        ListedCharge(description="Code Execution Usage", usd=Decimal("0.02")),
        ListedCharge(description="Session usage", usd=Decimal("0.03")),
    )
    assert result.other_usd == Decimal("0.05")
    assert result.provider_usd == Decimal("1.00")
    assert result.residual_usd == Decimal(0)


def test_provider_tokens_map_openai_types_and_keep_unknown_null() -> None:
    lines = [
        line("1", token_type="input", quantity=100),
        line("1", token_type="cached_input", quantity=40),
        line("1", token_type="output", quantity=None),
        line("1", token_type="output", quantity=10),
    ]
    group = only_group(reconcile(lines, []))
    assert group.provider_tokens == TokenTotals(
        uncached_input=100, cache_read=40, cache_write_5m=0, cache_write_1h=0, output=None
    )


def test_variance_null_when_provider_zero() -> None:
    result = reconcile([], [row("0.30")])
    assert result.provider_usd == Decimal(0)
    assert result.variance_ratio is None
    assert result.max_abs_variance_ratio is None
    assert only_group(result).variance_ratio is None


def test_spanlight_usd_null_when_no_row_priced() -> None:
    group = only_group(reconcile([line("1.00")], [row(None, unpriced_calls=1)]))
    assert group.spanlight_usd is None
    assert group.residual_usd == Decimal("1.00")


def test_provisional_boundary_at_48h() -> None:
    boundary = DAY_END + timedelta(hours=48)
    second = timedelta(seconds=1)
    assert is_provisional(DAY, boundary - second, settled_fetch=True) is True
    assert is_provisional(DAY, boundary + second, settled_fetch=True) is False
    assert is_provisional(DAY, boundary, settled_fetch=True) is False
    assert is_provisional(DAY, boundary - second, settled_fetch=False) is True
    assert is_provisional(DAY, boundary + second, settled_fetch=False) is True

    assert reconcile([line("1")], [], now=boundary + second).provisional is False
    assert reconcile([line("1")], [], now=boundary - second).provisional is True
    assert reconcile([line("1")], [], settled_fetch=False).provisional is True


def test_group_key() -> None:
    assert group_key("gpt-4o", "gpt-4o-2024-08-06") == "gpt-4o"
    assert group_key(None, "gpt-4o-2024-08-06") == "unmapped:gpt-4o-2024-08-06"
    assert group_key(None, None) == "unmapped:"


def test_unmapped_names_never_merge_across_sides() -> None:
    provider_side = line("2.00", model_raw="gpt-4o-2024-08-06", model_pattern=None)
    spanlight_side = row(
        "1.90",
        provider="openai",
        provider_canonical="openai",
        model="gpt-4o",
        model_pattern=None,
    )
    result = reconcile(
        [replace(provider_side, provider="openai")], [spanlight_side], provider="openai"
    )
    groups = {group.group: group for group in result.groups}
    assert set(groups) == {"unmapped:gpt-4o", "unmapped:gpt-4o-2024-08-06"}
    assert groups["unmapped:gpt-4o-2024-08-06"].provider_models == ("gpt-4o-2024-08-06",)
    assert groups["unmapped:gpt-4o-2024-08-06"].spanlight_models == ()
    assert groups["unmapped:gpt-4o"].spanlight_models == ("gpt-4o",)
    assert groups["unmapped:gpt-4o"].provider_models == ()
    # An unmapped name never joins a pattern group, even when it would match by name.
    mapped = line("1.00", model_raw="gpt-4o", model_pattern="gpt-4o", provider="openai")
    keyed = reconcile([mapped], [spanlight_side], provider="openai")
    assert sorted(group.group for group in keyed.groups) == ["gpt-4o", "unmapped:gpt-4o"]


def test_identical_unmapped_name_is_one_group() -> None:
    provider_side = line("2.00", model_raw="claude-x-preview", model_pattern=None)
    spanlight_side = row(
        None, model="claude-x-preview", model_pattern=None, unpriced_calls=1, output_tokens=5
    )
    group = only_group(reconcile([provider_side], [spanlight_side]))
    assert group.group == "unmapped:claude-x-preview"
    assert group.model_pattern is None
    assert kinds(group) == ["unpriced_spans"]
    assert group.residual_usd == Decimal("2.00")


def test_rows_of_other_providers_and_days_are_ignored() -> None:
    rows = [
        row("1.00"),
        row("5.00", provider="openai", provider_canonical="openai"),
        row("7.00", day=DAY - timedelta(days=1)),
        row("9.00", provider="claude", provider_canonical="anthropic"),
    ]
    result = reconcile([line("10.00"), line("3.00", day=DAY + timedelta(days=1))], rows)
    group = only_group(result)
    assert group.spanlight_usd == Decimal("10.00")
    assert group.provider_usd == Decimal("10.00")


def test_day_totals_are_group_sums() -> None:
    lines = [
        line("10.00"),
        line("4.00", model_raw="claude-opus-4-8", model_pattern="claude-opus-4-8"),
        line("2.00", model_raw="claude-haiku-4-5", model_pattern="claude-haiku-4-5"),
        line("0.005", model_raw="claude-tiny", model_pattern="claude-tiny"),
    ]
    rows = [
        row("9.00"),
        row("4.20", model="claude-opus-4-8", model_pattern="claude-opus-4-8"),
        row("0.001", model="claude-tiny", model_pattern="claude-tiny"),
    ]
    result = reconcile(lines, rows)
    groups = result.groups
    assert [group.group for group in groups] == [
        "claude-haiku-4-5",
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-tiny",
    ]
    assert result.provider_usd == sum((group.provider_usd for group in groups), Decimal(0))
    assert result.spanlight_usd == Decimal("13.201")
    assert result.explained_usd == sum((group.explained_usd for group in groups), Decimal(0))
    assert result.explained_usd == Decimal("2.00")
    assert result.residual_usd == sum((group.residual_usd for group in groups), Decimal(0))
    assert result.residual_usd == (
        result.provider_usd - (result.spanlight_usd or 0) - result.explained_usd
    )
    assert result.variance_ratio == (result.residual_usd / result.provider_usd).quantize(
        Decimal("0.0001")
    )
    # claude-tiny is under one cent on the provider side, so its 80% gap does not count.
    assert result.max_abs_variance_ratio == Decimal("0.1000")
    assert result.credits == ()
    assert result.credits_usd == Decimal(0)


def test_empty_day() -> None:
    result = reconcile([], [])
    assert result.groups == ()
    assert result.spanlight_usd is None
    assert result.provider_usd == Decimal(0)
    assert result.residual_usd == Decimal(0)
    assert result.variance_ratio is None


def test_tier_notes_use_the_snapshot_in_force_on_the_day() -> None:
    # The 2026-10-15 snapshot has no tier table of its own, so a day under it resolves to the
    # oldest modelled table: Anthropic priority is commitment-billed there as from 2026-10-16,
    # matching how the spans of that day were priced.
    for day in (DAY - timedelta(days=1), DAY):
        rows = [row("1.00", day=day), row("0.40", day=day, service_tier="priority")]
        group = only_group(reconcile([line("1.00", day=day)], rows, day=day))
        assert explained(group, "priority_commitment").usd == Decimal("-0.40")
        assert "unmodelled_modifiers" not in kinds(group)
        assert group.residual_usd == Decimal(0)


def test_spanlight_output_tokens_unknown_when_any_call_is_unreported() -> None:
    # An unreported output count is stored as NULL and summed as 0 by the rollup; the comparison
    # must not read that as a known, low total.
    rows = [
        row("1.00", output_tokens=50, uncached_input_tokens=10),
        row(None, calls=1, priced_calls=0, usage_unreported_calls=1, uncached_input_tokens=5),
    ]
    totals = only_group(reconcile([line("1.00")], rows)).spanlight_tokens
    assert totals.output is None
    assert totals.uncached_input == 15
    known = only_group(reconcile([line("1.00")], rows[:1])).spanlight_tokens
    assert known.output == 50


def test_zero_priority_cost_is_not_negative_zero() -> None:
    rows = [row("1.00"), row("0", service_tier="priority")]
    item = explained(only_group(reconcile([line("1.00")], rows)), "priority_commitment")
    assert item.usd == Decimal(0)
    assert item.usd is not None and not item.usd.is_signed()

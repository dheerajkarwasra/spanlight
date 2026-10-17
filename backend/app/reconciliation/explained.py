"""The explained lines of one reconciliation group (spec §7.5). Pure: no I/O.

An explained line names one known cause of a gap between the provider's bill and Spanlight's
figure. Only `batch_spend`, `server_tool_fees`, `untraced_traffic` and `priority_commitment`
carry USD, because only they are computed from amounts on both sides (or, for priority, from
an amount the provider is known never to bill); the others state a count or a token total and
leave the USD to the residual, which is never folded into a line. A line appears only when it
has something to say (a non-zero amount, count or token total).

Spanlight's side of a comparison reads only rows whose `cost_usd` is known, and a row's
`cost_usd` already holds its `fee_usd` (§6.8): token dollars are `cost_usd - fee_usd`. The
rollup sums a fee only for spans whose cost is known, so this split is exact; a fee on a span
whose cost is unknown is in neither sum, so the provider's charge for it lands in the
`server_tool_fees` gap.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

from app.pricing.tiers import TierMultipliers
from app.reconciliation.types import CostRollupRow, MappedLine

__all__ = ["ExplainedKind", "ExplainedLine", "GroupInputs", "explain_group", "provider_unknown"]

ExplainedKind = Literal[
    "batch_spend",
    "server_tool_fees",
    "unpriced_spans",
    "usage_not_reported",
    "untraced_traffic",
    "priority_commitment",
    "unmodelled_modifiers",
    "beyond_retention",
]

_ZERO = Decimal(0)
_NOTE_QUANTUM = Decimal("0.0001")
_PAST_RETENTION = "spans for this day are past retention"
_STANDARD_RATE = "priced at the standard rate"


@dataclass(frozen=True)
class ExplainedLine:
    """One known cause of a gap; `usd` is `None` when the cause has no computable amount."""

    kind: ExplainedKind
    usd: Decimal | None
    count: int | None
    tokens: int | None
    note: str


@dataclass(frozen=True)
class GroupInputs:
    """One group's inputs: billed lines (`tokens`, `web_search`), priority usage, rollup rows."""

    lines: tuple[MappedLine, ...]
    priority: tuple[MappedLine, ...]
    rows: tuple[CostRollupRow, ...]


def explain_group(
    inputs: GroupInputs,
    provider_usd: Decimal,
    projects_beyond_retention: int,
    multipliers: TierMultipliers,
) -> tuple[ExplainedLine, ...]:
    """The group's explained lines, in the order of `ExplainedKind`.

    A group with no Spanlight call is explained by `untraced_traffic` alone: its batch and fee
    gaps are part of that amount, and listing them too would explain the same dollars twice.
    Past a mapped project's retention the batch and fee gaps carry no USD either: the spans
    behind them may have been deleted, and the gap must stay in the residual.
    `multipliers` is the tier table in force for the day, which says which tiers the provider
    bills under a commitment rather than per token.
    """
    rows = inputs.rows
    traced = sum(row.calls for row in rows) > 0
    beyond = projects_beyond_retention
    past = beyond > 0
    candidates = (
        _past_retention(_batch_spend(inputs.lines, rows), past) if traced else None,
        _past_retention(_server_tool_fees(inputs.lines, rows), past) if traced else None,
        _unpriced_spans(rows),
        _usage_not_reported(rows),
        None if traced else _untraced_traffic(provider_usd, past_retention=past),
        _priority_commitment(inputs, multipliers),
        _unmodelled_modifiers(rows),
        _beyond_retention(beyond),
    )
    return tuple(line for line in candidates if line is not None)


def _batch_spend(
    lines: Sequence[MappedLine], rows: Sequence[CostRollupRow]
) -> ExplainedLine | None:
    """Batch-tier token spend beyond the token cost of traced batch spans; never negative.

    Provider batch `tokens` lines hold no fees, so fees come off Spanlight's batch figure (they
    are compared in `server_tool_fees`). Spanlight recording more than the provider billed at
    the batch tier is not explained by untraced batch calls, so that excess stays in the
    residual.
    """
    billed = _sum_amounts(
        line for line in lines if line.line_kind == "tokens" and line.service_tier == "batch"
    )
    batch_rows = [row for row in rows if row.service_tier == "batch"]
    gap = billed - _sum_present(_token_cost(row) for row in batch_rows)
    if gap <= _ZERO:
        return None
    if any(row.unpriced_calls or row.usage_unreported_calls for row in batch_rows):
        note = "batch-tier spend not matched by priced batch spans (some have no cost)"
    else:
        note = "batch-tier spend not covered by traced batch calls"
    return ExplainedLine("batch_spend", gap, None, None, note)


def _server_tool_fees(
    lines: Sequence[MappedLine], rows: Sequence[CostRollupRow]
) -> ExplainedLine | None:
    """Provider web-search USD minus the fees of rows with a known cost (signed, spec §7.5).

    A fee on a row whose cost is unknown is not in `spanlight_usd`, so it is not subtracted.
    """
    searches = [line for line in lines if line.line_kind == "web_search"]
    recorded_fees = _sum_present(row.fee_usd for row in rows if row.cost_usd is not None)
    gap = _sum_amounts(searches) - recorded_fees
    if gap == _ZERO:
        return None
    billed = _sum_quantities(searches)
    recorded = sum(row.web_search_requests for row in rows)
    side = "web search spend" if billed is None else _counted(billed, "web search", "web searches")
    note = f"{side} on the provider side, {recorded:,} recorded"
    return ExplainedLine("server_tool_fees", gap, billed, None, note)


def _past_retention(line: ExplainedLine | None, past: bool) -> ExplainedLine | None:
    """The line without its USD when spans of the day are past a mapped project's retention."""
    if line is None or not past:
        return line
    note = f"{line.note}; {_PAST_RETENTION}"
    return ExplainedLine(line.kind, None, line.count, line.tokens, note)


def _unpriced_spans(rows: Sequence[CostRollupRow]) -> ExplainedLine | None:
    """Calls with model and counts but no price. Tokens are only stated for wholly unpriced rows.

    A rollup row mixing priced and unpriced calls cannot split its token sums, so the token
    total is unknown rather than an overstatement.
    """
    unpriced = [row for row in rows if row.unpriced_calls > 0]
    count = sum(row.unpriced_calls for row in unpriced)
    if count == 0:
        return None
    tokens = None
    if all(row.priced_calls == 0 for row in unpriced):
        tokens = sum(_row_tokens(row) for row in unpriced)
    note = f"{_counted(count, 'call', 'calls')} with no price for their model: cost unknown"
    return ExplainedLine("unpriced_spans", None, count, tokens, note)


def _usage_not_reported(rows: Sequence[CostRollupRow]) -> ExplainedLine | None:
    count = sum(row.usage_unreported_calls for row in rows)
    if count == 0:
        return None
    cut = sum(row.cut_stream_calls for row in rows)
    note = f"{_counted(count, 'call', 'calls')} whose output tokens were never reported"
    if cut:
        note += f" ({_counted(cut, 'cut stream', 'cut streams')})"
    return ExplainedLine("usage_not_reported", None, count, None, note)


def _untraced_traffic(provider_usd: Decimal, *, past_retention: bool) -> ExplainedLine | None:
    """The provider USD of a group with no Spanlight call.

    Past retention the spans may have existed and been deleted, so the line carries no USD and
    the gap stays in the residual: such a day must never read as a match.
    """
    if provider_usd <= _ZERO:
        return None
    if past_retention:
        note = f"no span with this model; {_PAST_RETENTION}"
        return ExplainedLine("untraced_traffic", None, None, None, note)
    return ExplainedLine("untraced_traffic", provider_usd, None, None, "no span with this model")


def _priority_commitment(inputs: GroupInputs, multipliers: TierMultipliers) -> ExplainedLine | None:
    """Commitment-billed priority calls: Spanlight's standard-rate token cost, negated.

    The cost report never bills a commitment-billed tier (Anthropic priority, §4.1), so the
    token cost Spanlight recorded for those calls is a known over-count. When the provider does
    bill a priority `tokens` line, both sides hold the calls and `usd` is `None`.
    """
    rows = [
        row
        for row in inputs.rows
        if row.cost_usd is not None
        and multipliers.note(row.provider_canonical, row.service_tier) == "commitment_billed"
    ]
    tokens = _sum_quantities(inputs.priority) if inputs.priority else None
    if not rows and (not inputs.priority or tokens == 0):
        return None
    calls = sum(row.calls for row in rows)
    recorded = _sum_present(_token_cost(row) for row in rows)
    billed = any(
        line.line_kind == "tokens" and line.service_tier == "priority" for line in inputs.lines
    )
    # `0 - x`, not `-x`: a zero token cost reads as 0, never as -0.
    usd = None if billed or not rows else _ZERO - recorded
    total = "priority tokens" if tokens is None else f"{tokens:,} priority tokens"
    if not rows:
        return ExplainedLine(
            "priority_commitment", None, None, tokens, f"{total}, commitment-billed"
        )
    note = f"{_counted(calls, 'priority call', 'priority calls')} {_STANDARD_RATE}"
    if billed:
        note += "; the provider bills this tier, so both sides are compared"
    else:
        amount = recorded.quantize(_NOTE_QUANTUM)
        note += f" (${amount}); commitment-billed, not in the provider cost report"
    if inputs.priority:
        note += f" ({total})"
    return ExplainedLine("priority_commitment", usd, calls, tokens, note)


def provider_unknown(line: ExplainedLine) -> ExplainedLine:
    """The line on a day no run has fetched: no USD, and a priority note that quotes none.

    Whether the provider bills the priority tier that day is unknown, so the standard-rate
    amount is not a known over-count and the note must not state it as one.
    """
    if line.kind != "priority_commitment" or line.count is None:
        return replace(line, usd=None)
    note = (
        f"{_counted(line.count, 'priority call', 'priority calls')} {_STANDARD_RATE}; "
        "commitment-billed, the provider's side is not fetched yet"
    )
    return ExplainedLine(line.kind, None, line.count, line.tokens, note)


def _unmodelled_modifiers(rows: Sequence[CostRollupRow]) -> ExplainedLine | None:
    count = sum(row.unmodelled_modifier_calls for row in rows)
    if count == 0:
        return None
    note = (
        f"{_counted(count, 'call', 'calls')} priced at the standard rate with a modifier "
        "Spanlight does not price (OpenAI priority, fast mode, data residency)"
    )
    return ExplainedLine("unmodelled_modifiers", None, count, None, note)


def _beyond_retention(projects: int) -> ExplainedLine | None:
    if projects == 0:
        return None
    mapped = _counted(projects, "mapped project", "mapped projects")
    note = f"spans for this day are past the retention of {mapped}"
    return ExplainedLine("beyond_retention", None, projects, None, note)


def _token_cost(row: CostRollupRow) -> Decimal | None:
    """A row's token dollars: its known cost without the fees it holds."""
    if row.cost_usd is None:
        return None
    return row.cost_usd - (row.fee_usd or _ZERO)


def _sum_amounts(lines: Iterable[MappedLine]) -> Decimal:
    return sum((line.amount_usd for line in lines), _ZERO)


def _sum_present(values: Iterable[Decimal | None]) -> Decimal:
    """The sum of the known values; an unknown one adds nothing (it is counted elsewhere)."""
    return sum((value for value in values if value is not None), _ZERO)


def _sum_quantities(lines: Sequence[MappedLine]) -> int | None:
    """The lines' total quantity, or `None` when any line's quantity is unknown."""
    quantities = [line.quantity for line in lines]
    if any(quantity is None for quantity in quantities):
        return None
    return sum(quantity for quantity in quantities if quantity is not None)


def _row_tokens(row: CostRollupRow) -> int:
    return (
        row.uncached_input_tokens
        + row.cache_read_tokens
        + row.cache_write_5m_tokens
        + row.cache_write_1h_tokens
        + row.output_tokens
    )


def _counted(count: int, singular: str, plural: str) -> str:
    return f"{count:,} {singular if count == 1 else plural}"

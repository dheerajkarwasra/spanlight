"""Reconciliation arithmetic: one provider's bill against Spanlight's figures for one UTC day.

Pure: no I/O (spec §7.5). The provider lines and the rollup rows are grouped by the seed or
override pattern both sides were mapped with; a name with no pattern groups as
`unmapped:<name>` under its exact spelling, so an alias never merges with its neighbour.

`residual_usd = provider_usd - (spanlight_usd or 0) - explained_usd` is always present and
signed: positive when Spanlight recorded less than the provider billed, negative when it
recorded more. Credits and charges Spanlight cannot see (`other`, `code_execution`) are listed
apart and never enter the provider figure, the residual or the variance. Day totals are the
sums of the groups.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from app.pricing.provider_alias import canonical_provider
from app.pricing.tiers import TierMultipliers, multipliers_for
from app.reconciliation.explained import ExplainedKind, ExplainedLine, GroupInputs, explain_group
from app.reconciliation.token_totals import TokenTotals, provider_tokens, spanlight_tokens
from app.reconciliation.types import CostRollupRow, MappedLine
from app.reconciliation.usage_merge import PRIORITY_DESCRIPTION

__all__ = [
    "MATERIAL_PROVIDER_USD",
    "NO_PROVIDER_LINE_NOTE",
    "SETTLE_LAG",
    "DayReconciliation",
    "ExplainedKind",
    "ExplainedLine",
    "GroupReconciliation",
    "ListedCharge",
    "TokenTotals",
    "group_key",
    "is_provisional",
    "reconcile_day",
]

# Provider data for a day may still change for 48 h after it ends (spec §13, provider lag).
SETTLE_LAG = timedelta(hours=48)
# Groups billed less than this are too small for their variance to mean anything (§14).
MATERIAL_PROVIDER_USD = Decimal("0.01")
UNMAPPED_PREFIX = "unmapped:"
# §13: Spanlight traced calls the provider billed nowhere in this source.
NO_PROVIDER_LINE_NOTE = (
    "no provider line: wrong source scope, or usage billed under another account"
)

_ZERO = Decimal(0)
_RATIO_QUANTUM = Decimal("0.0001")


@dataclass(frozen=True)
class GroupReconciliation:
    """One model group of one day. `variance_ratio` is `None` when the provider billed 0.

    `note` is `NO_PROVIDER_LINE_NOTE` when Spanlight has calls and the provider no line.
    """

    group: str
    model_pattern: str | None
    provider_models: tuple[str, ...]
    spanlight_models: tuple[str, ...]
    provider_usd: Decimal
    spanlight_usd: Decimal | None
    explained_usd: Decimal
    residual_usd: Decimal
    variance_ratio: Decimal | None
    provider_tokens: TokenTotals
    spanlight_tokens: TokenTotals
    explained: tuple[ExplainedLine, ...]
    note: str | None


@dataclass(frozen=True)
class ListedCharge:
    """A provider line shown apart from the comparison: a credit or a charge Spanlight can't see."""

    description: str
    usd: Decimal


@dataclass(frozen=True)
class DayReconciliation:
    """One UTC day. `incomplete` means spans of a mapped project were past retention.

    `unattributed_calls` counts calls on rollup rows with no provider: they cannot be put in
    any provider's report, so they are left out of every group and counted here instead.
    """

    day: date
    provisional: bool
    incomplete: bool
    provider_usd: Decimal
    spanlight_usd: Decimal | None
    explained_usd: Decimal
    residual_usd: Decimal
    variance_ratio: Decimal | None
    credits_usd: Decimal
    other_usd: Decimal
    groups: tuple[GroupReconciliation, ...]
    credits: tuple[ListedCharge, ...]
    other: tuple[ListedCharge, ...]
    max_abs_variance_ratio: Decimal | None
    unattributed_calls: int


@dataclass
class _Bucket:
    pattern: str | None
    lines: list[MappedLine] = field(default_factory=list)
    priority: list[MappedLine] = field(default_factory=list)
    rows: list[CostRollupRow] = field(default_factory=list)


def is_provisional(day: date, now: datetime, settled_fetch: bool) -> bool:
    """Whether the day's figures may still change.

    `settled_fetch` means a successful run covering the day finished after `day_end + 48 h`;
    the day settles only when both that run exists and the lag has passed. A naive `now` is
    a programming error (it cannot be placed against a UTC day) and raises `ValueError`.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return now < _day_start(day + timedelta(days=1)) + SETTLE_LAG or not settled_fetch


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time(), tzinfo=UTC)


def group_key(model_pattern: str | None, raw: str | None) -> str:
    """The pattern, or `unmapped:<raw>` (an absent name is `unmapped:`) when there is none."""
    if model_pattern is not None:
        return model_pattern
    return f"{UNMAPPED_PREFIX}{raw or ''}"


def reconcile_day(
    day: date,
    provider: str,
    provider_lines: Sequence[MappedLine],
    spanlight: Sequence[CostRollupRow],
    now: datetime,
    settled_fetch: bool,
    retention_cutoffs: Mapping[UUID, date],
) -> DayReconciliation:
    """Reconcile one day of one provider.

    Only lines and rows of `day` whose provider maps to `provider` (through
    `canonical_provider`) are read; a rollup row with no provider cannot be attributed and is
    counted in `unattributed_calls`. `retention_cutoffs` maps each mapped project to its first
    retained day. Tier notes come from the snapshot in force at 00:00 UTC of the day.
    """
    provisional = is_provisional(day, now, settled_fetch)
    canonical = canonical_provider(provider)
    lines = [
        line
        for line in provider_lines
        if line.day == day and canonical_provider(line.provider) == canonical
    ]
    of_day = [row for row in spanlight if row.day == day]
    rows = [row for row in of_day if row.provider_canonical == canonical]
    beyond = sum(1 for cutoff in retention_cutoffs.values() if day < cutoff)
    credits, other, buckets = _classify(lines, rows)
    multipliers = multipliers_for(None, _day_start(day))
    groups = tuple(
        _reconcile_group(key, buckets[key], beyond, multipliers)
        for key in sorted(buckets)
        if _has_content(buckets[key])
    )
    provider_usd = _sum(group.provider_usd for group in groups)
    explained_usd = _sum(group.explained_usd for group in groups)
    residual_usd = _sum(group.residual_usd for group in groups)
    return DayReconciliation(
        day=day,
        provisional=provisional,
        incomplete=beyond > 0,
        provider_usd=provider_usd,
        spanlight_usd=_sum_known(group.spanlight_usd for group in groups),
        explained_usd=explained_usd,
        residual_usd=residual_usd,
        variance_ratio=_variance(residual_usd, provider_usd),
        credits_usd=_sum(charge.usd for charge in credits),
        other_usd=_sum(charge.usd for charge in other),
        groups=groups,
        credits=credits,
        other=other,
        max_abs_variance_ratio=_max_abs_variance(groups),
        unattributed_calls=sum(row.calls for row in of_day if row.provider_canonical is None),
    )


def _classify(
    lines: Sequence[MappedLine], rows: Sequence[CostRollupRow]
) -> tuple[tuple[ListedCharge, ...], tuple[ListedCharge, ...], dict[str, _Bucket]]:
    """Split the lines into credits, other charges and model groups; add the rows to the groups."""
    credits: list[ListedCharge] = []
    other: list[ListedCharge] = []
    buckets: dict[str, _Bucket] = {}
    for line in lines:
        if line.line_kind == "credit":
            credits.append(ListedCharge(line.description, line.amount_usd))
        elif _is_priority_usage(line):
            _bucket(buckets, line.model_pattern, line.model_raw).priority.append(line)
        elif line.line_kind in ("other", "code_execution"):
            other.append(ListedCharge(line.description, line.amount_usd))
        else:
            _bucket(buckets, line.model_pattern, line.model_raw).lines.append(line)
    for row in rows:
        _bucket(buckets, row.model_pattern, row.model).rows.append(row)
    return _listed(credits), _listed(other), buckets


def _is_priority_usage(line: MappedLine) -> bool:
    """A synthetic §7.4 line: priority-tier tokens the cost report does not bill."""
    return (
        line.line_kind == "other"
        and line.service_tier == "priority"
        and line.description == PRIORITY_DESCRIPTION
        and line.amount_usd == _ZERO
    )


def _bucket(buckets: dict[str, _Bucket], pattern: str | None, raw: str | None) -> _Bucket:
    return buckets.setdefault(group_key(pattern, raw), _Bucket(pattern))


def _has_content(bucket: _Bucket) -> bool:
    """False for a group holding only priority usage of zero tokens: nothing to show."""
    return bool(bucket.lines or bucket.rows) or any(line.quantity != 0 for line in bucket.priority)


def _listed(charges: list[ListedCharge]) -> tuple[ListedCharge, ...]:
    return tuple(sorted(charges, key=lambda charge: (charge.description, charge.usd)))


def _reconcile_group(
    key: str, bucket: _Bucket, beyond: int, multipliers: TierMultipliers
) -> GroupReconciliation:
    inputs = GroupInputs(tuple(bucket.lines), tuple(bucket.priority), tuple(bucket.rows))
    provider_usd = _sum(line.amount_usd for line in inputs.lines)
    spanlight_usd = _sum_known(row.cost_usd for row in inputs.rows)
    explained = explain_group(inputs, provider_usd, beyond, multipliers)
    explained_usd = _sum(item.usd for item in explained if item.usd is not None)
    residual_usd = provider_usd - (spanlight_usd or _ZERO) - explained_usd
    provider_models = {line.model_raw for line in (*inputs.lines, *inputs.priority)}
    return GroupReconciliation(
        group=key,
        model_pattern=bucket.pattern,
        provider_models=_names(provider_models),
        spanlight_models=_names({row.model for row in inputs.rows}),
        provider_usd=provider_usd,
        spanlight_usd=spanlight_usd,
        explained_usd=explained_usd,
        residual_usd=residual_usd,
        variance_ratio=_variance(residual_usd, provider_usd),
        provider_tokens=provider_tokens(inputs.lines),
        spanlight_tokens=spanlight_tokens(inputs.rows),
        explained=explained,
        note=_group_note(inputs),
    )


def _group_note(inputs: GroupInputs) -> str | None:
    unbilled = not inputs.lines and not inputs.priority
    return NO_PROVIDER_LINE_NOTE if unbilled and sum(r.calls for r in inputs.rows) > 0 else None


def _variance(residual_usd: Decimal, provider_usd: Decimal) -> Decimal | None:
    if provider_usd <= _ZERO:
        return None
    return (residual_usd / provider_usd).quantize(_RATIO_QUANTUM, rounding=ROUND_HALF_UP)


def _max_abs_variance(groups: Sequence[GroupReconciliation]) -> Decimal | None:
    ratios = [
        abs(group.variance_ratio)
        for group in groups
        if group.provider_usd >= MATERIAL_PROVIDER_USD and group.variance_ratio is not None
    ]
    return max(ratios, default=None)


def _names(names: Iterable[str | None]) -> tuple[str, ...]:
    return tuple(sorted({name for name in names if name is not None}))


def _sum(values: Iterable[Decimal]) -> Decimal:
    return sum(values, _ZERO)


def _sum_known(values: Iterable[Decimal | None]) -> Decimal | None:
    """The sum of the known values, or `None` when none is known (unknown is never 0)."""
    known = [value for value in values if value is not None]
    return sum(known, _ZERO) if known else None

"""Turn the rows the context queries read into detector values. Pure.

Three jobs: normalise the text dimensions (an empty string is as unknown as NULL), parse the
flat `spanlight.gateway.*` attributes of a span into `GatewayAttrs`, and fold the grouped
rollup rows of the baseline period into the exact `Baselines` slices the detectors read.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.insights import format as fmt
from app.insights.context import ANY, BaselineKey, Baselines, Dimension, GatewayAttrs
from app.rollups.buckets import BUCKET_COUNT, approx_percentile

BASELINE_PERIOD = timedelta(days=7)
"""How much history a baseline covers."""

MIN_BASELINE_CALLS = 100
"""A slice with fewer llm calls than this in the baseline period has no baseline."""

GATEWAY_PREFIX = "spanlight.gateway."

# Attribute values are client-supplied (ingest does not reserve the namespace), so every number
# is bounded before it is converted: `int(Decimal("1e1000000"))` alone takes seconds of CPU on
# the event loop.
MAX_NUMBER_TEXT = 64
"""Longer attribute text is not a number we read."""
MAX_INT_DIGITS = 10
"""Status codes and attempt counts are small; more digits than this is not one."""
MAX_RETRY_AFTER_S = Decimal(86_400)
"""A `Retry-After` beyond a day is not a real one."""

# Attribute -> GatewayAttrs field. `spanlight.cache` and `spanlight.fault.*` are read too, but
# only a `spanlight.gateway.*` attribute makes a span a gateway span.
GATEWAY_ATTRIBUTES: Mapping[str, str] = {
    "spanlight.gateway.route": "route",
    "spanlight.gateway.target": "target",
    "spanlight.gateway.attempts": "attempts",
    "spanlight.cache": "cache",
    "spanlight.gateway.upstream_status": "upstream_status",
    "spanlight.gateway.retry_after_s": "retry_after_s",
    "spanlight.gateway.client_disconnected": "client_disconnected",
    "spanlight.fault.scenario": "fault_scenario",
    "spanlight.fault.profile_id": "fault_profile_id",
}


def blank_to_none(value: str | None) -> str | None:
    """`None` for NULL and for an empty string: both mean the value is unknown."""
    return value if value else None


def gateway_attrs(values: Mapping[str, str | None], *, is_gateway: bool) -> GatewayAttrs | None:
    """The span's gateway attributes, from their text form (`attributes ->> key`).

    `None` when the span has no `spanlight.gateway.*` attribute at all (`is_gateway`). A value
    that does not parse is treated as absent, never as 0.
    """
    if not is_gateway:
        return None
    return GatewayAttrs(
        route=blank_to_none(values.get("route")),
        target=blank_to_none(values.get("target")),
        attempts=_small_int(values.get("attempts")),
        cache=blank_to_none(values.get("cache")),
        upstream_status=_small_int(values.get("upstream_status")),
        retry_after_s=_retry_after(values.get("retry_after_s")),
        client_disconnected=_bool(values.get("client_disconnected")),
        fault_scenario=blank_to_none(values.get("fault_scenario")),
        fault_profile_id=blank_to_none(values.get("fault_profile_id")),
    )


def _decimal(raw: str | None) -> Decimal | None:
    """A finite number from short text; None otherwise. The magnitude is the caller's check."""
    if raw is None or len(raw) > MAX_NUMBER_TEXT:
        return None
    try:
        value = Decimal(raw.strip())
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _small_int(raw: str | None) -> int | None:
    """A non-negative whole number of at most `MAX_INT_DIGITS` digits; None otherwise (never
    converted)."""
    value = _decimal(raw)
    if value is None or value.adjusted() >= MAX_INT_DIGITS or value < 0:
        return None
    if value != value.to_integral_value():
        return None
    return int(value)


def parse_retry_after(raw: str | None) -> Decimal | None:
    """Seconds in `[0, MAX_RETRY_AFTER_S]` to the millisecond; None otherwise. Quantizing also
    drops an extreme exponent (`0E-999999`) that later arithmetic could trip on."""
    value = _decimal(raw)
    if value is None or value.adjusted() > MAX_RETRY_AFTER_S.adjusted():
        return None
    if not Decimal(0) <= value <= MAX_RETRY_AFTER_S:
        return None
    return value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


_retry_after = parse_retry_after


def _bool(raw: str | None) -> bool:
    return raw is not None and raw.strip().lower() == "true"


@dataclass(frozen=True, slots=True)
class BaselineGroup:
    """One (environment, provider, model) group of llm rollup rows over the baseline period."""

    environment: str | None
    provider: str | None
    model: str | None
    calls: int
    errors: int
    cost_usd: Decimal | None  # NULL when no call in the group was priced
    latency_buckets: list[int]
    active_hours: frozenset[datetime]  # the hours of the group with at least one call


@dataclass(slots=True)
class _Slice:
    calls: int = 0
    errors: int = 0
    cost_usd: Decimal | None = None
    latency: list[int] = field(default_factory=lambda: [0] * BUCKET_COUNT)
    active_hours: set[datetime] = field(default_factory=set)

    def add(self, group: BaselineGroup) -> None:
        self.active_hours |= group.active_hours
        self.calls += group.calls
        self.errors += group.errors
        if group.cost_usd is not None:
            self.cost_usd = (self.cost_usd or Decimal(0)) + group.cost_usd
        self.latency = [a + b for a, b in zip(self.latency, group.latency_buckets, strict=True)]


def build_baselines(groups: Iterable[BaselineGroup]) -> Baselines:
    """The slices the detectors read, each from at least `MIN_BASELINE_CALLS` llm calls.

    * `error_rate` per (environment, model), any provider: errors / calls.
    * `p95_ms` per model, any environment and provider: estimated from the latency histogram,
      failed calls included (the rollups do not split it by status), rounded to 0.1 ms.
    * `cost_usd_per_hour` per environment: priced cost / active hours; absent when no call in
      the slice was priced.
    * `llm_calls_per_hour` per environment: calls / active hours.

    Active hours are the hours of the period in which the slice had at least one call. Idle
    hours (nights, weekends, the time before a project existed) would pull a per-hour baseline
    down, and a normal busy hour or a nightly batch would then look like a spike.
    """
    error_slices: dict[BaselineKey, _Slice] = {}
    p95_slices: dict[BaselineKey, _Slice] = {}
    environment_slices: dict[BaselineKey, _Slice] = {}
    for group in groups:
        environment, model = blank_to_none(group.environment), blank_to_none(group.model)
        _slice(error_slices, "error_rate", environment, model).add(group)
        _slice(p95_slices, "p95_ms", ANY, model).add(group)
        _slice(environment_slices, "environment", environment, ANY).add(group)

    values: dict[BaselineKey, Decimal] = {}
    for key, totals in error_slices.items():
        if totals.calls >= MIN_BASELINE_CALLS:
            values[key] = Decimal(totals.errors) / Decimal(totals.calls)
    for key, totals in p95_slices.items():
        p95 = approx_percentile(totals.latency, 0.95)
        if totals.calls >= MIN_BASELINE_CALLS and p95 is not None:
            values[key] = fmt.round_ms(Decimal(repr(p95)))
    for (_, slice_environment, _, _), totals in environment_slices.items():
        if totals.calls < MIN_BASELINE_CALLS:
            continue
        hours = Decimal(max(len(totals.active_hours), 1))
        calls_key: BaselineKey = ("llm_calls_per_hour", slice_environment, ANY, ANY)
        values[calls_key] = Decimal(totals.calls) / hours
        if totals.cost_usd is not None:
            values[("cost_usd_per_hour", slice_environment, ANY, ANY)] = totals.cost_usd / hours
    return Baselines(values)


def _slice(
    slices: dict[BaselineKey, _Slice], metric: str, environment: Dimension, model: Dimension
) -> _Slice:
    return slices.setdefault((metric, environment, ANY, model), _Slice())

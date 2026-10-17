"""How alert values are written for people: rates, milliseconds, money, counts, durations.

Pure and locale-free, shared by the Slack message, the alert email and the weekly digest. Numbers
group thousands with a thin space (U+2009), so a value reads the same in every locale and never
breaks across a line at a comma. An unknown value is the caller's to render ("—"); these
functions take a real number.
"""

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.alerts.types import Comparator, Metric

THIN_SPACE = "\u2009"

METRIC_LABELS: dict[Metric, str] = {
    Metric.ERROR_RATE: "Error rate",
    Metric.P95_MS: "p95 latency",
    Metric.TTFT_P95_MS: "p95 time to first token",
    Metric.COST_USD: "Cost",
    Metric.LLM_CALLS: "LLM calls",
    Metric.TOKENS: "Tokens",
    Metric.SPEND: "Spend",
    Metric.ERROR_CALLS: "Failed LLM calls",
}

COMPARATOR_WORDS: dict[Comparator, str] = {
    Comparator.GT: "above",
    Comparator.GTE: "at or above",
    Comparator.LT: "below",
    Comparator.LTE: "at or below",
}

COMPARATOR_SYMBOLS: dict[Comparator, str] = {
    Comparator.GT: ">",
    Comparator.GTE: "≥",
    Comparator.LT: "<",
    Comparator.LTE: "≤",
}

# Filter keys that read better short, in "env=production".
_FILTER_KEY_ALIASES = {"environment": "env"}


def format_count(value: Decimal) -> str:
    """A whole number with thin-space thousands: `1 234 567`."""
    whole = int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return f"{whole:,}".replace(",", THIN_SPACE)


def format_rate(value: Decimal) -> str:
    """A 0..1 rate as a percent with one decimal: `8.3 %`."""
    percent = (value * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    return f"{percent} %"


def format_ms(value: Decimal) -> str:
    """Milliseconds as a whole number: `1 240 ms`."""
    return f"{format_count(value)} ms"


def format_money(value: Decimal, *, plain: bool = False) -> str:
    """Dollars with two decimals, or four below one cent: `$1.50`, `$0.0042`.

    Thousands group with a thin space; `plain=True` groups them with a comma instead, for text
    that is not a message to a person (an API error).
    """
    if value != 0 and abs(value) < Decimal("0.01"):
        text = f"{value.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP):,.4f}"
    else:
        text = f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"
    return "$" + (text if plain else text.replace(",", THIN_SPACE))


def format_duration(duration: timedelta) -> str:
    """The two largest non-zero units, from days down to minutes: `1 h 20 min`, `2 d 3 h`.

    Under a minute reads `under 1 min`; a negative duration counts as zero.
    """
    minutes = max(int(duration.total_seconds()) // 60, 0)
    if minutes == 0:
        return "under 1 min"
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    parts = [(days, "d"), (hours, "h"), (mins, "min")]
    shown = [f"{amount} {unit}" for amount, unit in parts if amount]
    return " ".join(shown[:2])


def format_metric_value(metric: Metric, value: Decimal) -> str:
    """A metric's value in its own unit."""
    if metric is Metric.ERROR_RATE:
        return format_rate(value)
    if metric in (Metric.P95_MS, Metric.TTFT_P95_MS):
        return format_ms(value)
    if metric in (Metric.COST_USD, Metric.SPEND):
        return format_money(value)
    return format_count(value)


def filter_pairs(filters: dict[str, str]) -> list[str]:
    """A rule's filters as `key=value` text, sorted by key: `["env=production"]`."""
    return [f"{_FILTER_KEY_ALIASES.get(key, key)}={filters[key]}" for key in sorted(filters)]

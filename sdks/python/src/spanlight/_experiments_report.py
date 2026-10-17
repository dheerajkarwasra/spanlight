"""Scoring a finished run against its thresholds, and the Markdown report of it. Pure: no I/O."""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

_PLACES = Decimal("0.000001")
_UNKNOWN = "—"
_INCONCLUSIVE = "Interval includes the threshold; the result is not conclusive at this sample size."


def fmt(value: Decimal) -> str:
    """A number without trailing zeros: ``0.75``, ``1``, ``10``."""
    return format(value.quantize(_PLACES).normalize(), "f")


def _mean(stat: Any) -> Decimal | None:
    """The mean of a numeric score; ``None`` for a categorical score or a missing one."""
    mean = stat.get("mean") if isinstance(stat, dict) else None
    if isinstance(mean, bool) or not isinstance(mean, int | float):
        return None
    return Decimal(str(mean))


def _interval(stat: Any) -> tuple[Decimal, Decimal] | None:
    """The 95 % interval on a boolean score's pass rate.

    ``None`` for a numeric or categorical score, or from a server that predates intervals.
    """
    if not isinstance(stat, dict):
        return None
    try:
        low, high = Decimal(str(stat["ci_low"])), Decimal(str(stat["ci_high"]))
    except (KeyError, InvalidOperation):  # a missing key, or null
        return None
    if not (low.is_finite() and high.is_finite()):  # "NaN" or "Infinity" would raise in floor
        return None
    return low, high


def _percents(interval: tuple[Decimal, Decimal] | None) -> str:
    """``64\u201388%``: the low bound floored, the high bound ceiled, so the range contains it."""
    if interval is None:
        return _UNKNOWN
    low, high = interval
    return f"{math.floor(low * 100)}\u2013{math.ceil(high * 100)}%"


def check_thresholds(totals: dict[str, Any], fail_below: dict[str, Decimal]) -> list[str]:
    """One line per score whose mean is below its threshold or that the run did not produce.

    A score nobody recorded (every item errored, or the evaluator was over budget) fails the
    gate: an unknown value never counts as a pass.
    """
    scores = totals.get("scores") or {}
    failures: list[str] = []
    for name, threshold in fail_below.items():
        mean = _mean(scores.get(name))
        if mean is None:
            failures.append(f"{name}: no numeric score recorded < {fmt(threshold)}")
        elif mean < threshold:
            failures.append(f"{name}: {fmt(mean)} < {fmt(threshold)}")
    return failures


def _score_rows(totals: dict[str, Any], fail_below: dict[str, Decimal]) -> list[str]:
    scores = totals.get("scores") or {}
    rows = [
        "| Score | Mean | 95% CI | Items | Threshold | Result |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name in sorted({*scores, *fail_below}):
        stat = scores.get(name)
        mean = _mean(stat)
        count = stat.get("count") if isinstance(stat, dict) else None
        threshold = fail_below.get(name)
        if threshold is None:
            result = ""
        elif mean is not None and mean >= threshold:
            result = "pass"
        else:
            result = "FAIL"
        rows.append(
            f"| {name} | {fmt(mean) if mean is not None else _UNKNOWN} "
            f"| {_percents(_interval(stat))} "
            f"| {count if count is not None else _UNKNOWN} "
            f"| {'>= ' + fmt(threshold) if threshold is not None else _UNKNOWN} | {result} |"
        )
    return rows


def _inconclusive(totals: dict[str, Any], fail_below: dict[str, Decimal]) -> list[str]:
    """One line per gated boolean score whose threshold lies inside its interval.

    The gate still compares the mean; this only says the verdict could flip with more items.
    """
    scores = totals.get("scores") or {}
    lines: list[str] = []
    for name, threshold in sorted(fail_below.items()):
        interval = _interval(scores.get(name))
        if interval is not None and interval[0] <= threshold <= interval[1]:
            lines.append(f"{name}: {_INCONCLUSIVE}")
    return lines


def _number(value: Any) -> str:
    return _UNKNOWN if value is None else str(value)


def _milliseconds(value: Any) -> str:
    """A latency in whole milliseconds; the server sends a raw float such as ``1234.5678``."""
    if value is None:
        return _UNKNOWN
    if isinstance(value, bool) or not isinstance(value, int | float):
        return str(value)
    try:
        return str(int(Decimal(str(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP)))
    except (InvalidOperation, ValueError, OverflowError):
        return _UNKNOWN


def render_markdown(
    *,
    title: str,
    status: str,
    error: str | None,
    url: str,
    totals: dict[str, Any] | None,
    fail_below: dict[str, Decimal],
    failures: list[str],
) -> str:
    """The report printed to the log and appended to the GitHub step summary."""
    verdict = "passed" if status == "done" and not failures else "failed"
    lines = [
        f"### Spanlight eval gate: {title}",
        "",
        f"Gate **{verdict}**. [Open the run]({url})",
        "",
    ]
    if status != "done":
        lines += [f"The run did not finish ({status}): {error or 'no reason given'}.", ""]
    if totals is not None:
        lines += [
            f"Items {_number(totals.get('items'))}, errors {_number(totals.get('errors'))}, "
            f"cost {_number(totals.get('cost_usd'))} USD, "
            f"p50 {_milliseconds(totals.get('latency_p50_ms'))} ms, "
            f"p95 {_milliseconds(totals.get('latency_p95_ms'))} ms.",
            "",
            *_score_rows(totals, fail_below),
            "",
        ]
        if inconclusive := _inconclusive(totals, fail_below):
            lines += [*inconclusive, ""]
    if failures:
        lines += ["Below threshold:", "", *[f"- {line}" for line in failures], ""]
    return "\n".join(lines)

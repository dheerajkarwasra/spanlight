"""A ``Retry-After`` that is not a finite number falls back to the jittered backoff.

Failure mode: ``float("nan")`` parses, the retry delay becomes NaN, and ``Event.wait(nan)``
returns at once, so the exporter retries with no backoff at all.
"""

from __future__ import annotations

import math

import pytest

from spanlight._exporter import BatchExporter, parse_retry_after


@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "-inf", "infinity"])
def test_retry_after_nan_is_ignored(value: str) -> None:
    assert parse_retry_after(value) is None


def test_a_non_finite_retry_after_still_waits_a_finite_backoff() -> None:
    exporter = BatchExporter(url="http://spanlight.test/v1/traces", api_key="k")
    try:
        delay = exporter._delivery.retry_delay(0, parse_retry_after("nan"))
    finally:
        exporter.shutdown()

    assert math.isfinite(delay) and delay > 0


def test_a_numeric_retry_after_is_still_honoured() -> None:
    assert parse_retry_after(" 7 ") == 7.0
    assert parse_retry_after("-3") == 0.0

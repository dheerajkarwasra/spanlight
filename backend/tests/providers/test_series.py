"""The pure folding behind the provider health routes: class order, error rate, buckets."""

from datetime import datetime, timedelta
from decimal import Decimal

from app.api.window import TimeWindow
from app.providers.series import ERROR_CLASS_ORDER, bucket_series, error_rate


def test_the_error_classes_are_the_ten_in_the_page_order() -> None:
    assert ERROR_CLASS_ORDER == (
        "rate_limit",
        "provider_5xx",
        "timeout",
        "network",
        "auth",
        "context_length",
        "content_filter",
        "client",
        "gateway",
        "unknown",
    )


def test_error_rate_is_unknown_without_calls_and_a_decimal_otherwise() -> None:
    assert error_rate(0, 0) is None
    assert error_rate(0, 4) == Decimal("0")
    assert error_rate(1, 3) == Decimal("0.333333")


def test_day_buckets_merge_hours_and_stay_gapless() -> None:
    day = datetime.fromisoformat("2026-10-14T00:00:00+00:00")
    span = TimeWindow(day + timedelta(hours=22), day + timedelta(days=2, hours=1))
    hours = {(day + timedelta(hours=23), "timeout"): 2, (day + timedelta(days=1), "timeout"): 1}
    buckets = bucket_series(span, "day", hours, rollup_hours={day + timedelta(hours=23)})
    assert [(b.bucket_start, b.by_class, b.approximate) for b in buckets] == [
        (day, {"timeout": 2}, True),
        (day + timedelta(days=1), {"timeout": 1}, False),
        (day + timedelta(days=2), {}, False),
    ]

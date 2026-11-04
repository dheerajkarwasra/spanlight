"""The pure rules of the per-project ingest caps (`app.ingest.quota`)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.ingest.quota import (
    IngestCaps,
    Usage,
    describe_limit,
    effective_caps,
    exceeded,
    next_midnight,
    raw_span_pairs,
    seconds_until,
    span_limiter,
    utc_day,
    utilisation,
)


def test_overrides_replace_settings_and_zero_settings_are_unlimited(settings: Settings) -> None:
    zero_rate = settings.model_copy(update={"ingest_spans_per_second": 0})

    caps = effective_caps(zero_rate, None, 20, None)

    assert caps == IngestCaps(
        spans_per_second=None,
        spans_per_day=20,
        bytes_per_day=5 * 1024**3,
        overridden=frozenset({"spans_per_day"}),
    )
    assert (caps.source("spans_per_day"), caps.source("bytes_per_day")) == ("project", "default")
    assert span_limiter(caps) is None


def test_the_span_bucket_bursts_at_least_a_full_batch() -> None:
    slow = span_limiter(IngestCaps(1, None, None))
    fast = span_limiter(IngestCaps(20_000, None, None))

    assert slow is not None and (slow.rate, slow.burst) == (1, 5000)
    assert fast is not None and (fast.rate, fast.burst) == (20_000, 20_000)


def test_exceeded_reports_spans_before_bytes_and_only_at_the_limit() -> None:
    caps = IngestCaps(None, 10, 100)

    assert exceeded(caps, Usage(9, 100)) == ("bytes_per_day", 100, 100)
    assert exceeded(caps, Usage(10, 100)) == ("spans_per_day", 10, 10)
    assert exceeded(caps, Usage(9, 99)) is None
    assert exceeded(IngestCaps(None, None, None), Usage(10**9, 10**12)) is None


def test_utilisation_is_the_closest_quota() -> None:
    assert utilisation(IngestCaps(None, 10, 100), Usage(2, 75)) == 0.75
    assert utilisation(IngestCaps(5, None, None), Usage(2, 75)) is None


def test_raw_pairs_are_lowercased_and_distinct_and_refuse_non_string_ids() -> None:
    spans = [
        {"trace_id": "AB" * 16, "span_id": "CD" * 8},
        {"trace_id": "ab" * 16, "span_id": "cd" * 8},
        {"trace_id": "0" * 32, "span_id": "0" * 16},
    ]

    assert raw_span_pairs(spans) == [("0" * 32, "0" * 16), ("ab" * 16, "cd" * 8)]
    assert raw_span_pairs([*spans, {"trace_id": 12, "span_id": "cd" * 8}]) is None
    assert raw_span_pairs([*spans, "not a span"]) is None


def test_the_quota_resets_at_the_next_utc_midnight() -> None:
    now = datetime(2026, 11, 2, 23, 59, 59, 400_000, UTC)

    assert next_midnight(now) == datetime(2026, 11, 3, tzinfo=UTC)
    # A clock reporting in another zone keys the same UTC day as `resets_at`.
    east = datetime(2026, 11, 3, 1, 30, tzinfo=timezone(timedelta(hours=2)))
    assert utc_day(east) == datetime(2026, 11, 2, tzinfo=UTC).date()
    assert next_midnight(east) == datetime(2026, 11, 3, tzinfo=UTC)
    assert seconds_until(next_midnight(now), now) == 1
    assert seconds_until(next_midnight(now), datetime(2026, 11, 3, tzinfo=UTC)) == 1


@pytest.mark.parametrize(
    ("quota", "limit", "expected"),
    [
        ("spans_per_day", 1_000_000, "1,000,000 spans"),
        ("bytes_per_day", 5 * 1024**3, "5 GiB"),
        ("bytes_per_day", 512 * 1024**2, "512 MiB"),
        ("bytes_per_day", 4096, "4,096 bytes"),
    ],
)
def test_limits_are_described_in_the_unit_the_operator_set(
    quota: str, limit: int, expected: str
) -> None:
    assert describe_limit(quota, limit) == expected  # type: ignore[arg-type]  # the Literal

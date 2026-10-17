"""`reason_bucket` maps every rejection text the ingest pipeline produces to its loss reason.

The texts are the ones `pipeline._prepare` and `normalize` return today, as the ingest tests
assert them (`tests/ingest/test_ingest*.py`).
"""

import pytest

from app.ingest.pipeline import _UNPROCESSABLE
from app.telemetry_losses.reasons import (
    GATEWAY_REASONS,
    INGEST_REASONS,
    SDK_REASONS,
    reason_bucket,
)


@pytest.mark.parametrize(
    ("text", "bucket"),
    [
        pytest.param(
            "trace_id: String should match pattern '^[0-9a-f]{32}$'", "validation", id="validation"
        ),
        pytest.param("usage.input_tokens: Input should be a valid integer", "usage", id="usage"),
        pytest.param(
            "start_time: older than the maximum retention of 90 days", "too_old", id="too_old"
        ),
        pytest.param("end_time: is in the future", "in_future", id="in_future"),
        pytest.param(
            "parent_span_id: a span cannot be its own parent", "self_parent", id="self_parent"
        ),
        pytest.param("attributes: exceed 65536 bytes", "attributes_too_large", id="attributes"),
        pytest.param(
            "usage: the cost at this model's price is too large to store",
            "cost_too_large",
            id="cost_too_large",
        ),
        pytest.param(
            "duplicate span_id in batch; the later occurrence was kept",
            "duplicate_in_batch",
            id="duplicate_in_batch",
        ),
        pytest.param("span: could not be processed", "unprocessable", id="unprocessable"),
        pytest.param("", "other", id="other"),
    ],
)
def test_each_ingest_reason_has_a_real_rejection_text(text: str, bucket: str) -> None:
    assert reason_bucket(text) == bucket


def test_the_parametrised_cases_cover_every_ingest_reason() -> None:
    assert len(INGEST_REASONS) == 10
    assert INGEST_REASONS[-1] == "other"


@pytest.mark.parametrize(
    ("text", "bucket"),
    [
        # The semantic usage checks in `normalize`.
        ("usage.cached_tokens: cannot exceed usage.input_tokens", "usage"),
        (
            "usage.cache_write_tokens: cached_tokens + cache_write_tokens cannot exceed "
            "usage.input_tokens",
            "usage",
        ),
        ("usage.cache_write_1h_tokens: requires usage.cache_write_tokens", "usage"),
        ("usage.service_tier: String should have at most 64 characters", "usage"),
        # Schema errors on a field the semantic checks also use stay `validation`.
        ("start_time: Value error, timestamp must include a UTC offset", "validation"),
        ("end_time: Value error, timestamp is out of range", "validation"),
        ("end_time: must not be before start_time", "validation"),
        ("usage: Input should be a valid dictionary or instance of UsageIn", "validation"),
        ("span: Input should be a valid dictionary or instance of SpanIn", "validation"),
        ("attributes.my key: Input should be a valid string", "validation"),
        # Not a `<location>: <message>` text.
        ("something went wrong", "other"),
        (": no location", "other"),
        ("trace_id:", "other"),
    ],
)
def test_other_real_texts_land_in_the_right_bucket(text: str, bucket: str) -> None:
    assert reason_bucket(text) == bucket


def test_the_unprocessable_text_is_the_pipelines_own() -> None:
    assert reason_bucket(_UNPROCESSABLE) == "unprocessable"


def test_every_bucket_is_in_the_ingest_vocabulary() -> None:
    texts = ["x: y", "usage.a: b", "", "span: could not be processed"]
    assert {reason_bucket(text) for text in texts} <= set(INGEST_REASONS)


def test_gateway_and_sdk_vocabularies() -> None:
    assert GATEWAY_REASONS == ("overflow", "error", "rejected")
    assert SDK_REASONS == ("dropped", "failed")

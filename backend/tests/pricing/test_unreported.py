"""The `usage.unreported_reason` values are spelled once (spec §8.6).

Failure mode: the rollup counts one set of reasons while the cost explanation words another, so a
new reason is explained on the span card but never counted in `usage_unreported_calls`, or the
reverse.
"""

from app.pricing.explain_copy import UNREPORTED_REASON_COPY
from app.pricing.unreported import CUT_STREAM_REASONS, UNREPORTED_REASONS


def test_one_set_of_reasons_everywhere() -> None:
    assert set(UNREPORTED_REASONS) == {
        "client_disconnected",
        "stream_error",
        "stream_cut_by_fault",
        "include_usage_not_set",
        "no_final_usage",
        "not_exported",
    }
    assert set(UNREPORTED_REASON_COPY) == set(UNREPORTED_REASONS)


def test_cut_streams_are_the_three_stream_reasons() -> None:
    assert CUT_STREAM_REASONS == ("client_disconnected", "stream_error", "stream_cut_by_fault")
    assert set(CUT_STREAM_REASONS) < set(UNREPORTED_REASONS)

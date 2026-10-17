"""The `usage.unreported_reason` values (spec §8.6), spelled once. Pure: constants only.

A span carries one of them when its output tokens were not reported, so its cost is unknown. The
cost explanation words each one (`explain_copy`), the daily cost rollup counts them, and ingestion
validates against the same set.
"""

from typing import Literal, get_args

UnreportedReason = Literal[
    "client_disconnected",
    "stream_error",
    "stream_cut_by_fault",
    "include_usage_not_set",
    "no_final_usage",
    "not_exported",
]

UNREPORTED_REASONS: tuple[UnreportedReason, ...] = get_args(UnreportedReason)
# The reasons that mean a stream was cut before its final usage event.
CUT_STREAM_REASONS: tuple[UnreportedReason, ...] = (
    "client_disconnected",
    "stream_error",
    "stream_cut_by_fault",
)

"""Where reviewed traces first went wrong: the failing-span rollup.

A reviewer marks the span where a trace first failed by giving its `failure_category` score a
`span_id`. Over the traces that started in the window, each one counts once, under its latest
label (`app.review.failing_span_queries`); a label that names a span and is not `none` counts for
that span's name and kind. The share is over every reviewed trace of the window, with the Wilson
95 % interval of `app.evals.stats`. The window may span at most 30 days, like the release read.
"""

import uuid
from datetime import timedelta
from fractions import Fraction

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.review import FailingSpanOut, FailingSpansOut
from app.api.window import TimeWindow
from app.core.errors import ProblemError
from app.evals.stats import quantize_rate, wilson_interval
from app.review.failing_span_queries import MAX_GROUPS, read_failing_spans
from app.scores.failure_category import FAILURE_CATEGORY_SCORE

MAX_WINDOW = timedelta(days=30)


async def failing_spans(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow
) -> FailingSpansOut:
    """The window's failing spans, the most labelled first (at most `MAX_GROUPS`)."""
    if window.length > MAX_WINDOW:
        raise ProblemError(
            422, "WINDOW_TOO_LARGE", "The time window may span at most 30 days here."
        )
    reviewed, groups = await read_failing_spans(db, project_id, window)
    spans = []
    for group in groups[:MAX_GROUPS]:
        interval = wilson_interval(group.count, reviewed)
        if interval is None:  # unreachable: a group is a reviewed trace
            raise ValueError("reviewed must be positive")
        spans.append(
            FailingSpanOut(
                span_name=group.span_name,
                span_kind=group.span_kind,
                category=group.label,
                count=group.count,
                rate=quantize_rate(Fraction(group.count, reviewed)),
                ci_low=interval.low,
                ci_high=interval.high,
            )
        )
    return FailingSpansOut(
        score_name=FAILURE_CATEGORY_SCORE,
        reviewed=reviewed,
        spans=spans,
        truncated=len(groups) > MAX_GROUPS,
    )

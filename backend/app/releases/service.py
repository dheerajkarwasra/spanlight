"""Release comparison use-cases.

A release is the `release` field of the traces that started inside the window. The figures are
read from the raw spans of those traces (exact percentiles), whatever the window, which is why the
window may span at most 30 days. Definitions match the metrics overview: `llm_calls`,
`error_rate`, `unpriced_calls` and latency are over spans of kind `llm`; `cost_usd` and tokens are
over all spans of the traces; `cost_usd` is the priced subtotal, `None` when nothing was priced.
"""

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.releases import (
    CategoryRateOut,
    Comparison,
    FailureCategoriesOut,
    NewError,
    ReleaseRatesOut,
    ReleaseStats,
)
from app.api.window import TimeWindow
from app.core.errors import ProblemError, not_found
from app.releases import failure_category_queries, queries
from app.releases.compare import compare
from app.releases.failure_categories import ReleaseRates, category_rates
from app.releases.schemas import ErrorClassCount, ModelCount
from app.scores.failure_category import FAILURE_CATEGORY_SCORE, failure_category_config
from app.scores.types import ScoreDataType

MAX_WINDOW = timedelta(days=30)


def _stats(row: Row[Any]) -> ReleaseStats:
    llm_calls = int(row.llm_calls)
    return ReleaseStats(
        release=row.release,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        traces=int(row.traces),
        llm_calls=llm_calls,
        unpriced_calls=int(row.unpriced_calls),
        error_rate=int(row.llm_errors) / llm_calls if llm_calls else None,
        p50_ms=row.p50_ms,
        p95_ms=row.p95_ms,
        cost_usd=None if row.cost_usd is None else Decimal(row.cost_usd),
        input_tokens=int(row.input_tokens),
        output_tokens=int(row.output_tokens),
    )


def _require_window(window: TimeWindow) -> None:
    if window.length > MAX_WINDOW:
        raise ProblemError(
            422, "RELEASE_WINDOW_TOO_LARGE", "The time window may span at most 30 days here."
        )


async def list_releases(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow, environment: str | None
) -> list[ReleaseStats]:
    """The releases seen in the window, the one seen last first (at most 200)."""
    _require_window(window)
    rows = await queries.read_release_stats(db, project_id, window, environment)
    return [_stats(row) for row in rows]


async def compare_releases(
    db: AsyncSession,
    project_id: uuid.UUID,
    a: str,
    b: str,
    window: TimeWindow,
    environment: str | None,
) -> Comparison:
    """`b` against the baseline `a`. 422 for the same release twice, 404 for one never seen."""
    _require_window(window)
    if a == b:
        raise ProblemError(422, "SAME_RELEASE", "Choose two different releases to compare.")
    pair = (a, b)
    stats = {
        row.release: _stats(row)
        for row in await queries.read_release_stats(db, project_id, window, environment, only=pair)
    }
    for release in pair:
        if release not in stats:
            raise ProblemError(
                404, "UNKNOWN_RELEASE", "No traces carry this release in the window."
            )
    models = await queries.read_model_counts(db, project_id, window, environment, pair)
    classes = await queries.read_error_classes(db, project_id, window, environment, pair)
    new_errors = await queries.read_new_errors(db, project_id, window, environment, pair)
    return compare(
        stats[a],
        stats[b],
        [ModelCount(row.model, int(row.calls)) for row in models if row.release == a],
        [ModelCount(row.model, int(row.calls)) for row in models if row.release == b],
        [ErrorClassCount(row.error_class, int(row.n)) for row in classes if row.release == a],
        [ErrorClassCount(row.error_class, int(row.n)) for row in classes if row.release == b],
        [
            NewError(message=row.message, count=int(row.n), example_trace_id=row.example_trace_id)
            for row in new_errors
        ],
    )


def _rates_out(entry: ReleaseRates) -> ReleaseRatesOut:
    return ReleaseRatesOut(
        release=entry.release,
        reviewed=entry.reviewed,
        rates={
            label: CategoryRateOut(
                count=rate.count, rate=rate.rate, ci_low=rate.ci_low, ci_high=rate.ci_high
            )
            for label, rate in entry.rates.items()
        },
    )


async def failure_category_rates(
    db: AsyncSession, project_id: uuid.UUID, window: TimeWindow, environment: str | None
) -> FailureCategoriesOut:
    """The rate of each failure category per release of the window, the same releases and order
    as `list_releases` plus one `release: None` entry when traces without a release were seen.
    404 when the project has no categorical `failure_category` config."""
    _require_window(window)
    config = await failure_category_config(db, project_id)
    if config is None or config.data_type != ScoreDataType.CATEGORICAL:
        raise not_found("The project has no failure_category score config.")
    stats = await queries.read_release_stats(db, project_id, window, environment)
    reviewed, counts = await failure_category_queries.read_latest_failure_labels(
        db, project_id, window, environment
    )
    entries = category_rates(config.categories, [row.release for row in stats], reviewed, counts)
    return FailureCategoriesOut(
        score_name=FAILURE_CATEGORY_SCORE,
        categories=list(config.categories),
        releases=[_rates_out(entry) for entry in entries],
    )

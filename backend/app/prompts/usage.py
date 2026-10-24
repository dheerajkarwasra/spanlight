"""What a prompt version did over the last week, read from the raw spans that reference it.

The read goes through the partial index on `(project_id, prompt_version_id, started_at)`, so it
touches only the spans of those versions; the versions list reads a page of them at once.
Row-level security filters it like every project read.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.pagination import MAX_PAGE_SIZE
from app.db.models import Span, SpanKind

USAGE_WINDOW = timedelta(days=7)


@dataclass(frozen=True, slots=True)
class VersionUsage:
    """Unknown values are None: no span with a cost gives no cost, no LLM call gives no p95.

    `cost_usd` sums the priced spans only; `has_unpriced` says an LLM call in the window had no
    cost (unknown model or missing usage), so the sum is a lower bound. Both leave usage rollups
    (`spans.usage_rollup`) out; `llm_calls` and `traces` keep them.
    """

    traces: int
    llm_calls: int
    cost_usd: Decimal | None
    p95_ms: float | None
    has_unpriced: bool


async def version_usage(
    db: AsyncSession, project_id: uuid.UUID, version_id: uuid.UUID, now: datetime
) -> VersionUsage:
    """Traces, LLM calls, cost and p95 latency of the spans that used the version in the last
    seven days. Cost is the sum of the spans that have one and None when none does."""
    return (await versions_usage(db, project_id, [version_id], now))[version_id]


async def versions_usage(
    db: AsyncSession, project_id: uuid.UUID, version_ids: Sequence[uuid.UUID], now: datetime
) -> dict[uuid.UUID, VersionUsage]:
    """`version_usage` for each id in one grouped read; an id with no spans gets zero calls."""
    if len(version_ids) > MAX_PAGE_SIZE:
        raise ValueError(f"at most {MAX_PAGE_SIZE} version ids, got {len(version_ids)}")
    is_llm = Span.kind == SpanKind.LLM
    billable = Span.usage_rollup.is_(False)
    rows = (
        await db.execute(
            select(
                Span.prompt_version_id,
                func.count(distinct(Span.trace_id)),
                func.count().filter(is_llm),
                func.sum(Span.cost_usd).filter(billable),
                func.percentile_cont(0.95).within_group(Span.duration_ms).filter(is_llm),
                func.bool_or(and_(is_llm, billable, Span.cost_usd.is_(None))),
            )
            .where(
                Span.project_id == project_id,
                Span.prompt_version_id.in_(version_ids),
                Span.started_at >= now - USAGE_WINDOW,
            )
            .group_by(Span.prompt_version_id)
        )
    ).all()
    found = {
        row[0]: VersionUsage(
            traces=row[1],
            llm_calls=row[2],
            cost_usd=row[3],
            p95_ms=row[4],
            has_unpriced=bool(row[5]),
        )
        for row in rows
    }
    unused = VersionUsage(traces=0, llm_calls=0, cost_usd=None, p95_ms=None, has_unpriced=False)
    return {version_id: found.get(version_id, unused) for version_id in version_ids}

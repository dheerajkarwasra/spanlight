"""The SQL behind "Explain with Claude": credentials, trace excerpts, the budget and the rows.

Reads and writes of a project's own rows (`insight_explanations`, `spans`) rely on the
transaction being bound to the project. The budget's month-to-date sum covers every project of
the organization, so it is the database function `explain_month_to_date_cost` (migration 0507),
which sees past row-level security for its own body only: the request never sets the bypass
flag, and the reservation insert is checked by the table's policy. Callers commit.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from sqlalchemy import case, delete, select, text, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    InsightExplanation,
    ProviderCredential,
    ProviderKind,
    Span,
    SpanKind,
    SpanStatus,
)
from app.insights.explain import TraceExcerpt


async def oldest_credential(
    db: AsyncSession, org_id: uuid.UUID, provider: ProviderKind
) -> ProviderCredential | None:
    """The organization's first credential for `provider` (no row-level security: by `org_id`)."""
    return await db.scalar(
        select(ProviderCredential)
        .where(ProviderCredential.org_id == org_id, ProviderCredential.provider == provider)
        .order_by(ProviderCredential.created_at, ProviderCredential.id)
        .limit(1)
    )


async def trace_excerpts(
    db: AsyncSession, project_id: uuid.UUID, trace_ids: Sequence[str]
) -> list[TraceExcerpt]:
    """One span per trace, in the order of `trace_ids`: its first failed span, preferring llm
    spans, else its first llm span, else its first span. Traces with no stored span are left out.
    """
    if not trace_ids:
        return []
    failed_first = case((Span.status == SpanStatus.ERROR, 0), else_=1)
    llm_first = case((Span.kind == SpanKind.LLM, 0), else_=1)
    rows = (
        await db.scalars(
            select(Span)
            .where(Span.project_id == project_id, Span.trace_id.in_(list(trace_ids)))
            .ext(distinct_on(Span.trace_id))
            .order_by(Span.trace_id, failed_first, llm_first, Span.started_at, Span.span_id)
        )
    ).all()
    by_trace = {span.trace_id: span for span in rows}
    return [_excerpt(by_trace[trace_id]) for trace_id in trace_ids if trace_id in by_trace]


def _excerpt(span: Span) -> TraceExcerpt:
    return TraceExcerpt(
        trace_id=span.trace_id,
        model=span.model,
        status=span.status.value,
        error_class=span.error_class,
        status_message=span.status_message,
        finish_reason=span.finish_reason,
        input_tokens=span.input_tokens,
        output_tokens=span.output_tokens,
        input=span.input,
        output=span.output,
    )


async def lock_org_budget(db: AsyncSession, org_id: uuid.UUID) -> None:
    """Serialize the explain budget checks of one organization until the transaction ends."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('explain:' || :org_id))"),
        {"org_id": str(org_id)},
    )


async def month_to_date_cost(db: AsyncSession, org_id: uuid.UUID, since: datetime) -> Decimal:
    """The cost of the organization's explanations since `since`, reservations included.

    `org_id` must be the organization of the project the request was authorized for: the
    function sums whatever organization it is given.
    """
    total = await db.scalar(
        text("SELECT explain_month_to_date_cost(:org_id, :since)"),
        {"org_id": org_id, "since": since},
    )
    return Decimal(0) if total is None else Decimal(total)


async def insert_reservation(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    insight_id: uuid.UUID,
    model: str,
    cost_usd: Decimal,
    created_by: uuid.UUID | None,
    created_at: datetime,
) -> uuid.UUID:
    row = InsightExplanation(
        project_id=project_id,
        insight_id=insight_id,
        model=model,
        text=None,
        cost_usd=cost_usd,
        created_by=created_by,
        created_at=created_at,
        completed_at=None,
    )
    db.add(row)
    await db.flush()
    return row.id


async def complete_reservation(
    db: AsyncSession,
    reservation_id: uuid.UUID,
    *,
    text_: str | None,
    cost_usd: Decimal,
    completed_at: datetime,
) -> InsightExplanation | None:
    """Complete a reservation with the cost and, when there is one, the answer. `text_` None keeps
    a paid call without an answer in the budget. None when the row is gone (its insight was
    deleted)."""
    return await db.scalar(
        update(InsightExplanation)
        .where(InsightExplanation.id == reservation_id, InsightExplanation.completed_at.is_(None))
        .values(text=text_, cost_usd=cost_usd, completed_at=completed_at)
        .returning(InsightExplanation)
        # The reservation inserted earlier in this session is still in its identity map; the
        # returned row must replace its stale attributes, not be replaced by them.
        .execution_options(synchronize_session=False, populate_existing=True)
    )


async def delete_reservation(db: AsyncSession, reservation_id: uuid.UUID) -> None:
    await db.execute(
        delete(InsightExplanation).where(
            InsightExplanation.id == reservation_id, InsightExplanation.completed_at.is_(None)
        )
    )


async def list_completed(
    db: AsyncSession, project_id: uuid.UUID, insight_id: uuid.UUID, limit: int
) -> list[InsightExplanation]:
    """The insight's completed explanations with an answer, newest first."""
    rows = await db.scalars(
        select(InsightExplanation)
        .where(
            InsightExplanation.project_id == project_id,
            InsightExplanation.insight_id == insight_id,
            InsightExplanation.text.is_not(None),
        )
        .order_by(InsightExplanation.created_at.desc(), InsightExplanation.id.desc())
        .limit(limit)
    )
    return list(rows.all())

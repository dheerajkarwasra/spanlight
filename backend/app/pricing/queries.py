"""Reads over prices, the organization's price overrides, and the spans that found no price.

Nothing here writes; adding and deleting overrides is `app.pricing.overrides_service`.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelPrice, PriceOverride, User

# Models listed at most; a project cannot make the answer unbounded by sending arbitrary names.
UNPRICED_MODELS_LIMIT = 100

# An LLM span is unpriced for want of a price when it reports both token counts (so a cost could
# be computed) and a model, yet its stored cost is NULL: the model matched no price at ingestion.
# Spans that lack a token count or a model are unpriced for another reason and are not listed.
# Runs under the caller's row-level security binding; the `project_id` filter is for the index.
_UNPRICED_MODELS = text(
    """
    SELECT s.provider,
           s.model,
           count(*)                           AS llm_calls,
           coalesce(sum(s.input_tokens), 0)   AS input_tokens,
           coalesce(sum(s.output_tokens), 0)  AS output_tokens
    FROM spans AS s
    WHERE s.project_id = :project_id
      AND s.started_at >= :start
      AND s.started_at < :end
      AND s.kind = 'llm'
      AND s.cost_usd IS NULL
      AND s.model IS NOT NULL
      AND s.input_tokens IS NOT NULL
      AND s.output_tokens IS NOT NULL
    GROUP BY s.provider, s.model
    ORDER BY llm_calls DESC, s.provider NULLS LAST, s.model
    LIMIT :limit
    """
)


@dataclass(frozen=True)
class UnpricedModel:
    provider: str | None
    model: str
    llm_calls: int
    input_tokens: int
    output_tokens: int


async def list_prices(db: AsyncSession) -> list[ModelPrice]:
    """Every row of the price table, in a stable order."""
    rows = await db.scalars(
        select(ModelPrice).order_by(
            ModelPrice.provider, ModelPrice.model_pattern, ModelPrice.effective_from
        )
    )
    return list(rows)


async def list_unpriced_models(
    db: AsyncSession, project_id: uuid.UUID, *, start: datetime, end: datetime
) -> list[UnpricedModel]:
    """Models a project called in `[start, end)` that no price covered, most-called first."""
    rows = await db.execute(
        _UNPRICED_MODELS,
        {"project_id": project_id, "start": start, "end": end, "limit": UNPRICED_MODELS_LIMIT},
    )
    return [
        UnpricedModel(
            provider=row.provider,
            model=row.model,
            llm_calls=row.llm_calls,
            input_tokens=row.input_tokens,
            output_tokens=row.output_tokens,
        )
        for row in rows
    ]


async def list_overrides(
    db: AsyncSession, org_id: uuid.UUID
) -> Sequence[tuple[PriceOverride, User | None]]:
    """The organization's overrides with the user who added each, in a stable order.

    Unpaginated: an organization keeps a handful of overrides, not thousands. `price_overrides`
    has no row-level security, so the `org_id` condition is the tenancy boundary.
    """
    rows = await db.execute(
        select(PriceOverride, User)
        .outerjoin(User, User.id == PriceOverride.created_by)
        .where(PriceOverride.org_id == org_id)
        .order_by(
            PriceOverride.provider,
            PriceOverride.model_pattern,
            PriceOverride.effective_from,
            PriceOverride.id,
        )
    )
    return [(override, creator) for override, creator in rows]

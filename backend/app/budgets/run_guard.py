"""Per-run gateway circuit breakers (`x-spanlight-run-budget-usd` and `x-spanlight-max-steps`).

Pure check functions + Postgres-backed atomic reservation & settlement per `(project_id, trace_id)`.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.budgets.provider_bounds import requires_provider_bound
from app.budgets.reserve import (
    _may_have_billed,
    estimate_with_clamp,
)
from app.db.models import Span
from app.db.models.budgets import RunReservation
from app.evals.budget import failed_call_charge
from app.pricing.cost import load_price_book

RUN_TTL = timedelta(hours=24)
RUN_RESERVED_ATTR = "spanlight.run_reserved_usd"
_ZERO = Decimal(0)


async def estimate_run_cost(
    db: AsyncSession,
    org_id: UUID,
    body: Mapping[str, Any],
    model: str,
    target_providers: Sequence[str | None],
    target_models: Sequence[str],
    now: datetime,
    *,
    native_gemini: bool = False,
) -> tuple[Decimal | None, int | None]:
    """Worst-case cost estimate and optional output cap for a call guarded by a run budget."""
    prices = await load_price_book(db, org_id)
    cost, clamp = estimate_with_clamp(
        body, model, target_models, target_providers, prices, at=now, native_gemini=native_gemini
    )
    strict = native_gemini or requires_provider_bound(target_providers)
    return (cost if strict else max(cost or _ZERO, _ZERO)), clamp


def check_run_limits(
    step_count: int,
    settled_usd: Decimal,
    reserved_usd: Decimal,
    *,
    max_steps: int | None,
    run_budget_usd: Decimal | None,
    estimate_usd: Decimal,
) -> str | None:
    """Return refusal message if adding one step with `estimate_usd` breaks run caps, else None."""
    if max_steps is not None and step_count + 1 > max_steps:
        noun = "step" if max_steps == 1 else "steps"
        return f"Per-run step limit reached: at most {max_steps} {noun} allowed for this trace"
    if run_budget_usd is not None and settled_usd + reserved_usd + estimate_usd > run_budget_usd:
        return (
            f"Per-run budget of ${run_budget_usd:.4f} would be exceeded "
            f"(${settled_usd + reserved_usd:.4f} used/held)"
        )
    return None


async def reserve_run(
    db: AsyncSession,
    project_id: UUID,
    trace_id: str,
    *,
    max_steps: int | None,
    run_budget_usd: Decimal | None,
    estimate_usd: Decimal,
    now: datetime,
) -> str | None:
    """Atomically check and reserve one step/cost slot in `run_reservations` under row lock."""
    expires_at = now + RUN_TTL
    seed = pg_insert(RunReservation).values(
        project_id=project_id,
        trace_id=trace_id,
        step_count=0,
        reserved_usd=_ZERO,
        settled_usd=_ZERO,
        expires_at=expires_at,
    )
    await db.execute(seed.on_conflict_do_nothing(index_elements=["project_id", "trace_id"]))
    row = (
        await db.scalars(
            select(RunReservation)
            .where(RunReservation.project_id == project_id, RunReservation.trace_id == trace_id)
            .with_for_update()
        )
    ).one()
    refusal = check_run_limits(
        row.step_count,
        Decimal(row.settled_usd),
        Decimal(row.reserved_usd),
        max_steps=max_steps,
        run_budget_usd=run_budget_usd,
        estimate_usd=estimate_usd,
    )
    if refusal is not None:
        return refusal
    await db.execute(
        update(RunReservation)
        .where(RunReservation.project_id == project_id, RunReservation.trace_id == trace_id)
        .values(
            step_count=RunReservation.step_count + 1,
            reserved_usd=RunReservation.reserved_usd + estimate_usd,
            expires_at=expires_at,
        )
    )
    return None


async def settle_run(
    db: AsyncSession, project_id: UUID, span: Mapping[str, Any], now: datetime
) -> None:
    """Release the per-run reservation and record settled cost when the call's span is written."""
    attributes = span.get("attributes") or {}
    raw_reserved = attributes.get(RUN_RESERVED_ATTR)
    if raw_reserved is None:
        return
    try:
        reserved = Decimal(str(raw_reserved))
    except InvalidOperation:
        return
    trace_id = span.get("trace_id")
    span_id = span.get("span_id")
    if not isinstance(trace_id, str) or not isinstance(span_id, str):
        return
    stored = (
        await db.execute(
            select(Span.cost_usd).where(
                Span.project_id == project_id, Span.trace_id == trace_id, Span.span_id == span_id
            )
        )
    ).first()
    cost = None if stored is None else stored.cost_usd
    charge = (
        Decimal(cost)
        if cost is not None
        else failed_call_charge(
            may_have_billed=_may_have_billed(span), usage_cost=None, estimate=reserved
        )
    )
    await db.execute(
        text(
            "UPDATE run_reservations "
            "SET reserved_usd = GREATEST(0, reserved_usd - :reserved), "
            "settled_usd = settled_usd + :charge, "
            "expires_at = GREATEST(expires_at, :expires_at) "
            "WHERE project_id = :project_id AND trace_id = :trace_id"
        ),
        {
            "project_id": project_id,
            "trace_id": trace_id,
            "reserved": reserved,
            "charge": charge,
            "expires_at": now + RUN_TTL,
        },
    )

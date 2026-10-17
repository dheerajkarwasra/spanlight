"""The playground: run a prompt against up to three model configurations side by side.

Members run it (`experiments:run`). The calls go through the gateway in process, so they are
real spend in the project and appear as traces in the `playground` environment. Saving a
configuration as a prompt version is `POST /prompts/{name}/versions`.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.deps import Access, ClockDep, DbSession, SettingsDep, require
from app.api.schemas.playground import (
    PlaygroundResultOut,
    PlaygroundRunIn,
    PlaygroundRunOut,
    ReplayBudgetOut,
)
from app.core.idempotency import idempotent
from app.core.permissions import Permission
from app.core.ratelimit import enforce_rate_limit
from app.gateway.context import GatewayServices
from app.playground import replay_budget, service

router = APIRouter(prefix="/projects/{project_id}/playground", tags=["playground"])

PlaygroundRunner = Annotated[Access, Depends(idempotent(require(Permission.EXPERIMENTS_RUN)))]
PlaygroundReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]


@router.post("/runs", response_model=PlaygroundRunOut, summary="Run prompts in the playground")
async def run_playground(
    project_id: uuid.UUID,
    body: PlaygroundRunIn,
    request: Request,
    access: PlaygroundRunner,
    db: DbSession,
    clock: ClockDep,
) -> PlaygroundRunOut:
    """Run 1 to 3 configurations at once and answer with one result each, in order.

    A configuration that fails (a provider error, a missing variable) has its `error` and does
    not affect the others; a project budget that blocks a call is `error = "BUDGET_EXCEEDED"`.
    Responses are not streamed, so `ttft_ms` is null. `404` for a credential outside the
    organization or a prompt version outside the project; `409 NOT_CONFIGURED` without the
    gateway runtime or `CREDENTIALS_KEYS`; `429 RATE_LIMITED` after 30 runs a minute per user.
    """
    await enforce_rate_limit(
        request,
        service.PLAYGROUND_LIMITER,
        f"playground:{access.user_id}",
        scope="playground",
        detail="You ran the playground 30 times in the last minute. Try again shortly.",
    )
    services: GatewayServices | None = getattr(request.app.state, "gateway_runtime", None)
    results = await service.run(
        db,
        services,
        access.require_project(),
        access.user_id,
        body.configs,
        clock=clock,
    )
    return PlaygroundRunOut(results=[PlaygroundResultOut.of(result) for result in results])


@router.get("/replays/budget", response_model=ReplayBudgetOut, summary="Read the replay budget")
async def read_replay_budget(
    project_id: uuid.UUID,
    access: PlaygroundReader,
    db: DbSession,
    settings: SettingsDep,
    clock: ClockDep,
) -> ReplayBudgetOut:
    """The project's replay budget for the current UTC month: `REPLAY_MONTHLY_BUDGET_USD`, what
    settled replays cost, what running ones hold, and what is left."""
    budget = await replay_budget.read_budget(
        db,
        access.require_project().id,
        budget=settings.replay_monthly_budget_usd,
        now=clock(),
    )
    return ReplayBudgetOut.of(budget)

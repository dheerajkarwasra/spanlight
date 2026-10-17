"""Exact replay: send a stored LLM request again, through the gateway, under the project's
monthly replay budget (spec §6).

Members replay (`experiments:run`); viewers read what a replay would send and the ledger. The
calls are real spend in the project and appear as traces in the `playground` environment, named
`replay`. The budget itself is `GET /playground/replays/budget` (`api/v1/playground.py`).
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import Access, ClockDep, DbSession, SettingsDep, client_ip, require
from app.api.replay_problems import replay_problem
from app.api.schemas.common import Page
from app.api.schemas.replays import ReplayIn, ReplayOut, ReplaySourceOut, ReplaySummaryOut
from app.core.errors import not_found
from app.core.idempotency import idempotent
from app.core.observability import feature_used
from app.core.pagination import DEFAULT_PAGE_SIZE, PageLimit, decode_uuid_cursor, encode_cursor
from app.core.permissions import Permission
from app.core.ratelimit import enforce_rate_limit
from app.gateway.context import GatewayServices
from app.playground import replay_service
from app.playground.replay_errors import ReplayRefusedError
from app.playground.replay_queries import list_replays
from app.playground.replay_service import ReplayBudgetExceededError
from app.playground.service import PLAYGROUND_LIMITER

router = APIRouter(prefix="/projects/{project_id}", tags=["playground"])

ReplayRunner = Annotated[Access, Depends(idempotent(require(Permission.EXPERIMENTS_RUN)))]
ReplayReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]

SourceId = Annotated[str | None, Query(max_length=64)]


@router.get(
    "/traces/{trace_id}/spans/{span_id}/replay-source",
    response_model=ReplaySourceOut,
    summary="Read what a replay of a span would send",
)
async def read_replay_source(
    project_id: uuid.UUID, trace_id: str, span_id: str, access: ReplayReader, db: DbSession
) -> ReplaySourceOut:
    """Classify the span without calling a provider: its fidelity, surface, the credential
    family it needs and the stored request, or the refusal and its reason; plus its last ten
    replays. `404` when the span is not in the project."""
    view = await replay_service.read_source(db, access.require_project().id, trace_id, span_id)
    if view is None:
        raise not_found()
    return ReplaySourceOut.of(view)


@router.post("/playground/replays", response_model=ReplayOut, summary="Replay a stored request")
async def run_replay(
    project_id: uuid.UUID,
    body: ReplayIn,
    request: Request,
    access: ReplayRunner,
    db: DbSession,
    settings: SettingsDep,
    clock: ClockDep,
) -> ReplayOut:
    """Send the span's stored request once more with one of the organization's credentials of
    the same provider, never streamed, `n = 1`, at most 4096 output tokens.

    `404` for a span outside the project or a credential outside the organization; `409
    NOT_CONFIGURED` without the gateway runtime, `CREDENTIALS_KEYS` or a replay budget; `422
    REPLAY_*` when the span cannot be replayed as it was; `409 REPLAY_BUDGET_EXCEEDED` (with
    `spent_usd`, `reserved_usd`, `budget_usd` and `month_start`) when the month's budget cannot
    take the call; `429 RATE_LIMITED` after 30 playground runs a minute per user. A provider
    failure or a project budget block is the result's `error`, not an HTTP error.
    """
    await enforce_rate_limit(
        request,
        PLAYGROUND_LIMITER,
        f"playground:{access.user_id}",
        scope="playground",
        detail="You ran the playground 30 times in the last minute. Try again shortly.",
    )
    services: GatewayServices | None = getattr(request.app.state, "gateway_runtime", None)
    try:
        outcome = await replay_service.run_replay(
            db,
            services,
            access.require_project(),
            access.user_id,
            body,
            settings=settings,
            clock=clock,
            ip=client_ip(request),
        )
    except (ReplayRefusedError, ReplayBudgetExceededError) as error:
        raise replay_problem(error) from None
    feature_used("replay")
    return ReplayOut.of(outcome)


@router.get("/playground/replays", response_model=Page[ReplaySummaryOut], summary="List replays")
async def list_project_replays(
    project_id: uuid.UUID,
    access: ReplayReader,
    db: DbSession,
    trace_id: SourceId = None,
    span_id: SourceId = None,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
) -> Page[ReplaySummaryOut]:
    """The project's replays, newest first; `trace_id` and `span_id` narrow to one source."""
    after = decode_uuid_cursor(cursor)[1] if cursor else None
    rows = await list_replays(
        db,
        access.require_project().id,
        trace_id=trace_id.lower() if trace_id else None,
        span_id=span_id.lower() if span_id else None,
        after=after,
        limit=limit,
    )
    shown = rows[:limit]
    last = shown[-1] if shown else None
    return Page(
        items=[ReplaySummaryOut.of(row) for row in shown],
        next_cursor=encode_cursor(last.started_at, str(last.id))
        if len(rows) > limit and last
        else None,
    )

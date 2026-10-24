"""The Doctor's insights: list and read them, act on them, and ask Claude to explain one.

Anyone who can read the project reads insights, their explanations and the detector runs
(`project:read`; an API key does not); owners and admins act on them and ask for explanations
(`insights:manage`). Responses are built before the commit, which ends the project binding that
row-level security needs.
"""

import uuid
from dataclasses import asdict
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import Access, ClockDep, DbSession, client_ip, require
from app.api.locks import lock_project_of
from app.api.params import NO_NUL
from app.api.schemas import Page
from app.api.schemas.insight_feedback import feedback_summary_out
from app.api.schemas.insights import (
    DetectorRunOut,
    EvidenceSpanOut,
    ExplanationOut,
    HealthOut,
    InsightDetailOut,
    InsightOut,
    InsightSummaryOut,
    MuteIn,
    detector_run_out,
    explanation_out,
    health_out,
    insight_out,
)
from app.api.window import Window
from app.core.errors import not_found
from app.core.pagination import DEFAULT_PAGE_SIZE, PageLimit, decode_uuid_cursor, encode_cursor
from app.core.permissions import Permission
from app.gateway.context import GatewayServices
from app.insights import explain_queries, explain_service, feedback_queries, queries, service
from app.insights.health_service import project_health
from app.insights.schemas import InsightStatus, Severity
from app.insights.service import Actor

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/projects/{project_id}", tags=["insights"])

InsightReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]
InsightManager = Annotated[Access, Depends(require(Permission.INSIGHTS_MANAGE))]

MAX_RUNS = 100
MAX_EXPLANATIONS = 20
EnvironmentFilter = Annotated[str | None, Query(max_length=64, pattern=NO_NUL)]


@router.get("/insights", response_model=Page[InsightOut], summary="List insights")
async def list_insights(
    project_id: uuid.UUID,
    access: InsightReader,
    db: DbSession,
    status: Annotated[list[InsightStatus] | None, Query()] = None,
    severity: Annotated[Severity | None, Query()] = None,
    kind: Annotated[str | None, Query(min_length=1, max_length=64, pattern=NO_NUL)] = None,
    trace_id: Annotated[str | None, Query(min_length=1, max_length=64, pattern=NO_NUL)] = None,
    limit: PageLimit = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query()] = None,
) -> Page[InsightOut]:
    """Most recently seen first. `status` may repeat; `trace_id` keeps insights citing the trace."""
    rows = await queries.list_insights(
        db,
        access.require_project().id,
        statuses=status,
        severity=severity,
        kind=kind,
        trace_id=trace_id,
        after=decode_uuid_cursor(cursor) if cursor else None,
        limit=limit,
    )
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        next_cursor = encode_cursor(page[-1].last_seen_at, str(page[-1].id))
    return Page(items=[insight_out(row) for row in page], next_cursor=next_cursor)


@router.get(
    "/insights/summary", response_model=InsightSummaryOut, summary="Count the insights to act on"
)
async def insights_summary(
    project_id: uuid.UUID, access: InsightReader, db: DbSession
) -> InsightSummaryOut:
    """Open and acknowledged insights by severity, for the navigation badge."""
    counts = await queries.count_active_by_severity(db, access.require_project().id)
    return InsightSummaryOut(
        open_critical=counts.get(Severity.CRITICAL, 0),
        open_warning=counts.get(Severity.WARNING, 0),
        open_info=counts.get(Severity.INFO, 0),
    )


@router.get("/insights/{insight_id}", response_model=InsightDetailOut, summary="Read one insight")
async def get_insight(
    project_id: uuid.UUID, insight_id: uuid.UUID, access: InsightReader, db: DbSession
) -> InsightDetailOut:
    """The insight, its completed explanations, newest first (at most 20), and the feedback:
    the caller's own vote (`mine`, `null` when they have not voted) and every member's counts."""
    project_id = access.require_project().id
    insight = await queries.get_insight(db, project_id, insight_id)
    if insight is None:
        raise not_found()
    explanations = await explain_queries.list_completed(
        db, project_id, insight_id, MAX_EXPLANATIONS
    )
    feedback = await feedback_queries.feedback_summary(db, project_id, insight_id, access.user_id)
    detail = insight_out(insight)
    spans = await explain_queries.evidence_spans(db, project_id, detail.evidence.trace_ids)
    return InsightDetailOut(
        **detail.model_dump(),
        explanations=[explanation_out(row) for row in explanations],
        feedback=feedback_summary_out(feedback),
        evidence_spans=[EvidenceSpanOut(**asdict(span)) for span in spans],
    )


@router.post(
    "/insights/{insight_id}/acknowledge",
    response_model=InsightOut,
    summary="Acknowledge an insight",
)
async def acknowledge_insight(
    project_id: uuid.UUID,
    insight_id: uuid.UUID,
    request: Request,
    access: InsightManager,
    db: DbSession,
    clock: ClockDep,
) -> InsightOut:
    """Record that you have seen it. `409 INVALID_TRANSITION` unless it is open."""
    locked_project_id = await lock_project_of(access, db)
    insight = await service.acknowledge(
        db, locked_project_id, insight_id, _actor(access, request), now=clock()
    )
    return await _finish(db, access, insight_out(insight), "acknowledge")


@router.post(
    "/insights/{insight_id}/resolve", response_model=InsightOut, summary="Resolve an insight"
)
async def resolve_insight(
    project_id: uuid.UUID,
    insight_id: uuid.UUID,
    request: Request,
    access: InsightManager,
    db: DbSession,
    clock: ClockDep,
) -> InsightOut:
    """Mark it fixed. Detecting the problem again reopens it."""
    locked_project_id = await lock_project_of(access, db)
    insight = await service.resolve(
        db, locked_project_id, insight_id, _actor(access, request), now=clock()
    )
    return await _finish(db, access, insight_out(insight), "resolve")


@router.post("/insights/{insight_id}/mute", response_model=InsightOut, summary="Mute an insight")
async def mute_insight(
    project_id: uuid.UUID,
    insight_id: uuid.UUID,
    body: MuteIn,
    request: Request,
    access: InsightManager,
    db: DbSession,
    clock: ClockDep,
) -> InsightOut:
    """Silence it until `until` (at most 90 days ahead); `422 INVALID_MUTE` otherwise."""
    locked_project_id = await lock_project_of(access, db)
    insight = await service.mute(
        db,
        locked_project_id,
        insight_id,
        _actor(access, request),
        body.until,
        body.reason,
        now=clock(),
    )
    return await _finish(db, access, insight_out(insight), "mute")


@router.post(
    "/insights/{insight_id}/unmute", response_model=InsightOut, summary="Unmute an insight"
)
async def unmute_insight(
    project_id: uuid.UUID,
    insight_id: uuid.UUID,
    request: Request,
    access: InsightManager,
    db: DbSession,
) -> InsightOut:
    """End a mute early. `409 INVALID_TRANSITION` unless it is muted."""
    locked_project_id = await lock_project_of(access, db)
    insight = await service.unmute(db, locked_project_id, insight_id, _actor(access, request))
    return await _finish(db, access, insight_out(insight), "unmute")


@router.post(
    "/insights/{insight_id}/explain",
    response_model=ExplanationOut,
    status_code=201,
    summary="Ask Claude to explain an insight",
)
async def explain_insight(
    project_id: uuid.UUID,
    insight_id: uuid.UUID,
    request: Request,
    access: InsightManager,
    db: DbSession,
    clock: ClockDep,
) -> ExplanationOut:
    """Claude explains the insight from its evidence; the answer is advisory plain text.

    Sent with the organization's oldest Anthropic credential and counted against its monthly
    explain budget. `409 NOT_CONFIGURED` without a credential, `CREDENTIALS_KEYS` or a budget;
    `409 EXPLAIN_MODEL_UNPRICED`; `402 EXPLAIN_BUDGET_EXCEEDED`; `502 EXPLAIN_FAILED`.
    """
    services: GatewayServices | None = getattr(request.app.state, "gateway_runtime", None)
    project = access.require_project()
    row = await explain_service.explain(
        db, services, project, insight_id, _actor(access, request), clock=clock
    )
    logger.info(
        "insight_explain",
        org_id=str(access.org.id),
        project_id=str(project.id),
        insight_id=str(insight_id),
        model=row.model,
        cost_usd=str(row.cost_usd),
    )
    return explanation_out(row)


@router.get(
    "/detector-runs", response_model=list[DetectorRunOut], summary="List recent detector runs"
)
async def list_detector_runs(
    project_id: uuid.UUID,
    access: InsightReader,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=MAX_RUNS)] = MAX_RUNS,
) -> list[DetectorRunOut]:
    """Newest first: what ran, over which window, how many findings, and what failed."""
    runs = await queries.list_detector_runs(db, access.require_project().id, limit)
    return [detector_run_out(run) for run in runs]


@router.get("/health", response_model=HealthOut, summary="Score the project's health")
async def get_health(
    project_id: uuid.UUID,
    access: InsightReader,
    db: DbSession,
    window: Window,
    environment: EnvironmentFilter = None,
) -> HealthOut:
    """0 to 100 over the window (default the last 24 h); `value` is `null` without LLM calls."""
    health = await project_health(db, access.require_project().id, window, environment)
    return health_out(health, window.start, window.end)


def _actor(access: Access, request: Request) -> Actor:
    return Actor(org_id=access.org.id, user_id=access.user_id, ip=client_ip(request))


async def _finish(db: DbSession, access: Access, out: InsightOut, action: str) -> InsightOut:
    await db.commit()
    logger.info(
        "insight_" + action,
        org_id=str(access.org.id),
        project_id=str(access.require_project().id),
        insight_id=str(out.id),
    )
    return out

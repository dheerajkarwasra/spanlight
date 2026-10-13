"""Real demo traffic: a three-step support bot calling Claude, traced end to end.

Flow (`demo_flow`): classify the question (LLM) → look up a built-in FAQ entry
(retrieval) → answer from that entry (LLM). Every span records what really
happened: timestamps, the model the API reports, token usage from the
response, and errors. Nothing is simulated; without an ANTHROPIC_API_KEY, or
without CREDENTIALS_KEYS to seal it with, the job is skipped.

The two LLM calls go through the gateway in process (`execute`), with the
demo project's own gateway key (`ensure_demo_gateway`): their `llm` spans,
attributed to that key, land in the trace the flow started. Each is written
before the flow goes on; a span that cannot be stored fails the run before
any further provider call, since its cost would escape the monthly cap.
The root `chain` span and the `faq-lookup` retrieval span are written through
the ingestion pipeline function, with the same validation, redaction and
costing as SDK data. The key has no rate limits, cache or fault profile, and
its route makes one attempt with no fallback: a paid job never retries.

Spend is capped by DEMO_MONTHLY_BUDGET_USD using the costs recorded on the
demo project's spans this calendar month, the gateway's spans included.
"""

import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.budgets.guard import get_budget_guard
from app.config import Settings
from app.db.models import Span, SpanKind
from app.db.rls import bind_project
from app.gateway.budget import BudgetGuard
from app.gateway.cache import GatewayCache, PostgresGatewayCache
from app.gateway.context import context_for_key
from app.gateway.http import build_http_client
from app.ingest.pipeline import IngestTarget, ingest_spans
from app.jobs.context import TaskContext
from app.jobs.outcome import JobOutcome
from app.jobs.tasks.demo_flow import (
    QUESTIONS,
    DemoBudgetBlockedError,
    DemoSpendUnrecordedError,
    FlowRecorder,
    run_support_flow,
)
from app.services.demo_gateway import DemoGateway, ensure_demo_gateway

logger = structlog.get_logger(__name__)


def make_http_client(settings: Settings) -> httpx.AsyncClient:
    """The upstream client for one run. Separate so tests can put it on a mock transport."""
    return build_http_client(settings)


@dataclass(frozen=True)
class DemoServices:
    """The services a gateway context is built from (`GatewayServices`), for one job run.

    The worker keeps no process-wide resources for its tasks, so each run opens its own
    upstream client and closes it when the run ends. The budget guard is the real one, like every
    other in-process caller's: a firing `block` budget on the demo project stops a demo call, on
    top of the job's own monthly cap. The demo key has no cache TTL, so the cache is never read.
    """

    http: httpx.AsyncClient
    sessions: async_sessionmaker[AsyncSession]
    settings: Settings
    budget_guard: BudgetGuard
    cache: GatewayCache = field(default_factory=PostgresGatewayCache)


async def run_demo_traffic(context: TaskContext, _: dict[str, Any]) -> JobOutcome | None:
    reason = not_configured_reason(context.settings)
    if reason is not None:
        logger.info("demo_traffic_skipped", reason=reason)
        return JobOutcome.SKIPPED_NOT_CONFIGURED
    demo = await _demo_within_budget(context)
    if isinstance(demo, JobOutcome):
        return demo
    await context.heartbeat()  # make sure we still own the job before paying for calls
    question = random.choice(QUESTIONS)  # noqa: S311 - not security-sensitive
    async with make_http_client(context.settings) as http:
        services = DemoServices(
            http=http,
            sessions=context.session_factory,
            settings=context.settings,
            budget_guard=get_budget_guard(context.settings),
        )
        flow = await run_support_flow(
            context_for_key(demo.key_context, services, clock=context.clock), question
        )
    await _record_flow(context, demo, flow)
    return _flow_outcome(flow)


async def _demo_within_budget(context: TaskContext) -> DemoGateway | JobOutcome:
    """The demo gateway when a paid run may go ahead, or the outcome that skips the run."""
    async with context.session_factory() as db:
        demo = await ensure_demo_gateway(db, context.settings)
        await db.commit()
        if not makes_one_call(demo):
            # A paid job never retries, falls back or serves from a cache it did not set up.
            logger.warning(
                "demo_traffic_skipped",
                reason="demo gateway configuration changed",
                route_id=str(demo.route.id),
                key_id=str(demo.key_context.key.id),
                credential_id=str(demo.credential.id),
            )
            return JobOutcome.SKIPPED_NOT_CONFIGURED
        spent, unpriced = await month_to_date_spend(db, demo.workspace.project.id, context.clock())
    if unpriced:
        # Without recorded costs the budget cannot be enforced, so do not spend.
        logger.warning("demo_traffic_skipped", reason="unpriced demo calls this month")
        return JobOutcome.SKIPPED_BUDGET
    if spent >= context.settings.demo_monthly_budget_usd:
        logger.info("demo_traffic_skipped", reason="monthly budget reached", spent=str(spent))
        return JobOutcome.SKIPPED_BUDGET
    return demo


async def _record_flow(context: TaskContext, demo: DemoGateway, flow: FlowRecorder) -> None:
    """Write the flow's own spans (the root `chain` and the retrieval); the LLM spans are in."""
    workspace = demo.workspace
    async with context.session_factory() as db:
        outcome = await ingest_spans(
            db,
            IngestTarget(
                project_id=workspace.project.id,
                capture_payloads=True,
                org_id=workspace.org.id,
            ),
            flow.spans,
        )
        await db.commit()
    logger.info(
        "demo_traffic_recorded",
        trace_id=flow.trace_id,
        llm_calls=flow.llm_calls,
        accepted=outcome.accepted,
        rejected=len(outcome.rejected),
    )


def _flow_outcome(flow: FlowRecorder) -> JobOutcome:
    """The run's outcome; raises the flow's error when it failed (a paid job gets one attempt)."""
    if isinstance(flow.error, DemoBudgetBlockedError):
        # A firing project budget is a stop, not a failure: no spend, and the next run asks again.
        logger.warning("demo_traffic_skipped", reason="project budget blocked the demo call")
        return JobOutcome.SKIPPED_BUDGET
    if isinstance(flow.error, DemoSpendUnrecordedError):
        logger.error("demo_spend_unrecorded", trace_id=flow.trace_id, llm_calls=flow.llm_calls)
    if flow.error is not None:
        raise flow.error  # the run ends `failed`
    return JobOutcome.OK


def makes_one_call(demo: DemoGateway) -> bool:
    """Whether each demo call reaches the provider at most once, through `demo-anthropic`.

    `ensure_demo_gateway` reuses an existing route and key as they are, so the job checks before
    paying: one attempt, no fallback, a single target on the demo credential, the key on the
    `demo` route, and no cache TTL or fault profile on the key.
    """
    context, key = demo.key_context, demo.key_context.key
    route = context.route
    return (
        route.retry.max_attempts == 1
        and not route.fallback.on
        and len(route.targets) == 1
        and route.targets[0].credential_id == demo.credential.id
        and key.route_id == demo.route.id
        and context.route_id == demo.route.id
        and key.cache_ttl_seconds is None
        and key.fault_profile_id is None
        and context.fault_profile is None
    )


def not_configured_reason(settings: Settings) -> str | None:
    """Why the job cannot run with these settings, or None when it can."""
    if settings.anthropic_api_key is None:
        return "ANTHROPIC_API_KEY is not set"
    if not settings.is_demo_enabled:
        return "demo is disabled"
    if not settings.is_crypto_configured:
        # The provider key is stored sealed, like any credential; without the keyring it cannot be.
        return "credentials key not configured"
    return None


async def month_to_date_spend(
    db: AsyncSession, project_id: uuid.UUID, now: datetime
) -> tuple[Decimal, int]:
    """Recorded demo spend this month, and how many billed LLM calls lack a cost.

    Failed calls without usage are not billed, so they do not count as unpriced.
    """
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    await bind_project(db, project_id)
    row = (
        await db.execute(
            select(
                func.coalesce(func.sum(Span.cost_usd), 0),
                func.count().filter(
                    Span.kind == SpanKind.LLM,
                    Span.input_tokens.is_not(None),
                    Span.cost_usd.is_(None),
                ),
            ).where(Span.project_id == project_id, Span.started_at >= month_start)
        )
    ).one()
    spent, unpriced = row
    return spent if spent is not None else Decimal(0), int(unpriced)

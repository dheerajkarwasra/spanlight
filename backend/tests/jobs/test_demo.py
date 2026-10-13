"""The public demo: visitor sessions and the real-traffic demo job.

The demo job is exercised through the real in-process gateway on a mock
upstream transport (no network, no spend), so the request, response parsing,
span writing and usage recording are the production code paths.
"""

from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Span, Trace
from app.db.rls import bypass_rls
from app.jobs.outcome import JobOutcome
from app.jobs.tasks import TASKS, demo_traffic, seed_demo_evals
from app.main import create_app
from app.services.demo import ensure_demo_workspace
from app.services.demo_evals import NoAnthropicCredentialError
from tests.gateway_support import FakeProvider, ManualClock
from tests.jobs.demo_support import RUN_COST, TIGHT_CAP, keyed, run_context, script_flow

SessionFactory = async_sessionmaker[AsyncSession]


async def test_demo_session_is_read_only_viewer(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/demo/session")
    assert response.status_code == 200
    me = (await client.get("/api/v1/auth/me")).json()
    (membership,) = me["memberships"]
    assert membership["role"] == "viewer"
    assert membership["org"]["is_demo"] is True

    org_id = membership["org"]["id"]
    projects = (await client.get(f"/api/v1/orgs/{org_id}/projects")).json()
    csrf = {"X-CSRF-Token": client.cookies["spl_csrf"]}
    create_key = await client.post(
        f"/api/v1/projects/{projects[0]['id']}/keys", json={"name": "x"}, headers=csrf
    )
    assert create_key.status_code == 403


async def test_demo_session_expires_within_an_hour(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/demo/session")
    cookie = response.headers["set-cookie"]
    assert "Max-Age=3600" in cookie


async def test_demo_session_rate_limited_per_ip(client: httpx.AsyncClient) -> None:
    statuses = [(await client.post("/api/v1/demo/session")).status_code for _ in range(11)]
    assert statuses == [200] * 10 + [429]


async def test_demo_session_404_when_disabled(settings: Settings) -> None:
    disabled = settings.model_copy(update={"demo_enabled": False})
    app = create_app(disabled)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"Origin": "http://testserver"},
    ) as client:
        assert (await client.post("/api/v1/demo/session")).status_code == 404
    await app.state.engine.dispose()


async def _span_count(session_factory: SessionFactory) -> int:
    async with session_factory() as db:
        await bypass_rls(db)
        return int(await db.scalar(select(func.count()).select_from(Span)) or 0)


async def test_demo_traffic_skips_without_api_key(
    session_factory: SessionFactory, settings: Settings
) -> None:
    context = await run_context(session_factory, settings)  # settings has no key
    await demo_traffic.run_demo_traffic(context, {})
    assert await _span_count(session_factory) == 0


async def test_demo_traffic_records_real_usage(
    session_factory: SessionFactory, settings: Settings, demo_provider: FakeProvider
) -> None:
    script_flow(demo_provider)
    outcome = await demo_traffic.run_demo_traffic(
        await run_context(session_factory, keyed(settings)), {}
    )

    assert outcome is JobOutcome.OK
    assert [request.json["model"] for request in demo_provider.requests] == ["claude-haiku-4-5"] * 2
    assert {request.url for request in demo_provider.requests} == {
        "https://api.anthropic.com/v1/messages"
    }
    async with session_factory() as db:
        await bypass_rls(db)
        spans = (await db.scalars(select(Span).order_by(Span.started_at))).all()
        trace = (await db.scalars(select(Trace))).one()

    by_name = {span.name: span for span in spans if span.kind.value != "llm"}
    assert set(by_name) == {"support-bot", "faq-lookup"}
    root = by_name["support-bot"]
    assert by_name["faq-lookup"].kind.value == "retrieval"
    assert by_name["faq-lookup"].parent_span_id == root.span_id
    # The two LLM calls went through the gateway, which wrote their spans into the same trace.
    classify, answer = [span for span in spans if span.kind.value == "llm"]
    assert [classify.name, answer.name] == ["messages claude-haiku-4-5-20251001"] * 2
    assert classify.parent_span_id == answer.parent_span_id == root.span_id
    assert {span.trace_id for span in spans} == {root.trace_id}
    assert (classify.input_tokens, classify.output_tokens) == (40, 1)
    assert (answer.input_tokens, answer.output_tokens) == (120, 18)
    assert answer.model == "claude-haiku-4-5-20251001"
    # 120 * $1 + 18 * $5 per 1M tokens
    assert answer.cost_usd == Decimal("0.00021000")
    assert trace.environment == "demo" and "billing" in trace.tags
    # classify: 40 * $1 + 1 * $5; answer: 120 * $1 + 18 * $5 (per 1M tokens)
    assert trace.cost_usd == RUN_COST


async def test_demo_traffic_respects_monthly_budget(
    session_factory: SessionFactory,
    settings: Settings,
    demo_provider: FakeProvider,
    app_clock: ManualClock,
) -> None:
    script_flow(demo_provider)
    keyed_settings = keyed(settings, demo_monthly_budget_usd=TIGHT_CAP)
    first = await demo_traffic.run_demo_traffic(
        await run_context(session_factory, keyed_settings, app_clock), {}
    )
    assert first is JobOutcome.OK
    assert await _span_count(session_factory) == 4  # the first run spends $0.000255

    second = await demo_traffic.run_demo_traffic(
        await run_context(session_factory, keyed_settings, app_clock), {}
    )
    assert second is JobOutcome.SKIPPED_BUDGET
    assert await _span_count(session_factory) == 4  # budget reached: skipped, no API calls
    assert len(demo_provider.requests) == 2


async def test_demo_workspace_is_idempotent(session_factory: SessionFactory) -> None:
    async with session_factory() as db:
        first = await ensure_demo_workspace(db)
        second = await ensure_demo_workspace(db)
        await db.commit()
    assert first.project.id == second.project.id
    assert first.user.id == second.user.id


async def test_seed_demo_evals_job_is_registered() -> None:
    assert TASKS["seed_demo_evals"] is seed_demo_evals.run_seed_demo_evals


async def test_seed_demo_evals_job_fails_without_the_demo_credential(
    session_factory: SessionFactory, settings: Settings
) -> None:
    # No ANTHROPIC_API_KEY in the test settings, so the demo org never got its credential: the
    # job fails (the worker stores the reason on the job) and writes nothing.
    async with session_factory() as db:
        await ensure_demo_workspace(db)
        await db.commit()
    context = await run_context(session_factory, settings)
    with pytest.raises(NoAnthropicCredentialError):
        await seed_demo_evals.run_seed_demo_evals(context, {})

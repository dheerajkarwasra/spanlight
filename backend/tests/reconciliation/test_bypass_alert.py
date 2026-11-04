"""The bypass alert (hard gateway budgets design, D10): a closed UTC day whose provider bill
leaves more than 10 % and more than $1 unexplained tells the organization's admins once.

Failure modes: a leaked key or traffic outside the gateway and SDK goes unnoticed because only
the reconciliation page would show it; a refetch (the window is seven days) mails the same day
again; a day whose Spanlight side is unknown is reported as a residual it cannot have; a member
or another organization's admin is mailed about a source that is not theirs.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from app.config import Settings
from app.core.observability import BILLING_BYPASS_CHECKS
from app.db.models import JobStatus
from app.db.rls import bypass_rls
from app.jobs.tasks.cleanup import FINISHED_OUTBOX_RETENTION
from app.reconciliation import bypass_alert
from app.reconciliation.arithmetic import bypass_suspected
from app.reconciliation.fetch_policy import REFETCH_DAYS
from tests.gateway_support import metric_delta
from tests.helpers import create_workspace, join_with_role, verify_email
from tests.reconciliation.fetch_job_support import (
    Owner,
    SessionFactory,
    add_source,
    anthropic_api,
    payload,
    run_child,
    runs,
    serve,
)
from tests.reconciliation.fetch_support import (
    ANTHROPIC_COST_PATH,
    ANTHROPIC_USAGE_PATH,
    Reply,
    ScriptedApi,
    fixture_page,
    page,
    single_page,
)

OWNER, ADMIN, MEMBER, OUTSIDER = (
    "owner@example.com",
    "admin@example.com",
    "member@example.com",
    "outsider@example.com",
)
DAY_ONE = "2026-10-01"

_INSERT_SPAN = """
    WITH new_trace AS (
        INSERT INTO traces (project_id, trace_id, started_at, ended_at)
        VALUES (:project_id, :trace_id, :started_at, :started_at + interval '1 second')
    )
    INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, started_at, ended_at,
                       provider, model, input_tokens, output_tokens, cost_usd, pricing_version)
    VALUES (:project_id, :trace_id, :span_id, 'llm', 'call', 'ok', :started_at,
            :started_at + interval '1 second', 'anthropic', 'claude-sonnet-4-5', 100, 10,
            :cost, '2026-10-16')
"""


@pytest.fixture
async def owner(browser_factory: Any, session_factory: SessionFactory) -> Owner:
    """An owner, an admin and a member of one organization, all verified, plus the owner of
    another organization."""
    owner = await browser_factory(OWNER)
    admin = await browser_factory(ADMIN)
    member = await browser_factory(MEMBER)
    outsider = await browser_factory(OUTSIDER)
    workspace = await create_workspace(owner)
    await join_with_role(owner, admin, workspace.org_id, "admin")
    await join_with_role(owner, member, workspace.org_id, "member")
    await create_workspace(outsider, org_name="Elsewhere")
    for address in (OWNER, ADMIN, MEMBER, OUTSIDER):
        await verify_email(session_factory, address)
    return Owner(
        user_id=uuid.UUID(owner.user["id"]),
        org_id=uuid.UUID(workspace.org_id),
        project_id=uuid.UUID(workspace.project_id),
    )


@pytest.fixture
async def source_id(
    owner: Owner, session_factory: SessionFactory, gateway_settings: Settings
) -> uuid.UUID:
    return await add_source(session_factory, gateway_settings, owner)


async def _spent(session_factory: SessionFactory, project_id: uuid.UUID, cost: Decimal) -> None:
    """One priced Sonnet span of `cost` on the first fixture day."""
    async with session_factory() as db:
        await bypass_rls(db)
        await db.execute(
            text(_INSERT_SPAN),
            {
                "project_id": project_id,
                "trace_id": uuid.uuid4().hex,
                "span_id": uuid.uuid4().hex[:16],
                "started_at": datetime(2026, 10, 1, 12, tzinfo=UTC),
                "cost": cost,
            },
        )
        await db.commit()


def _one_line_api(amount: str) -> ScriptedApi:
    """A provider that billed one Sonnet input line of `amount` cents on the first fixture day."""
    line = {**fixture_page("anthropic_cost_page.json")["data"][0]["results"][0], "amount": amount}
    bucket = {
        "starting_at": "2026-10-01T00:00:00Z",
        "ending_at": "2026-10-02T00:00:00Z",
        "results": [line],
    }
    return ScriptedApi(
        routes={
            ANTHROPIC_COST_PATH: [Reply(json=page([bucket]))],
            ANTHROPIC_USAGE_PATH: single_page("anthropic_usage_page.json"),
        }
    )


async def _notices(session_factory: SessionFactory) -> list[dict[str, Any]]:
    """Every bypass notice in the outbox: recipient, summary and text, oldest first."""
    async with session_factory() as db:
        rows = await db.execute(
            text(
                "SELECT target->>'to' AS recipient, payload->'summary' AS summary, "
                "payload->>'text' AS body, channel_id FROM notification_outbox "
                "WHERE payload->'summary'->>'event' = 'billing_bypass' ORDER BY created_at, id"
            )
        )
        return [dict(row) for row in rows.mappings()]


async def test_unexplained_residual_notifies_admins_once_per_source_and_day(
    source_id: uuid.UUID,
    owner: Owner,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    serve(monkeypatch, anthropic_api())  # about $4.36 and $3.40 billed; Spanlight recorded nothing
    await run_child(session_factory, gateway_settings, payload(source_id))

    notices = await _notices(session_factory)
    assert {(n["recipient"], n["summary"]["day"]) for n in notices} == {
        (OWNER, DAY_ONE),
        (ADMIN, DAY_ONE),
        (OWNER, "2026-10-02"),
        (ADMIN, "2026-10-02"),
    }
    assert all(n["channel_id"] is None for n in notices)  # transactional mail, not a channel
    first = next(n for n in notices if n["summary"]["day"] == DAY_ONE)
    assert first["summary"]["source_id"] == str(source_id)
    assert Decimal(first["summary"]["residual_usd"]) > Decimal(4)
    assert first["summary"]["spanlight_usd"] is None  # no priced calls, not a fake "0"
    assert "Spanlight recorded no priced calls" in first["body"]
    for cause in ("outside the gateway and the SDK", "not mapped", "leaked"):
        assert cause in first["body"], first["body"]

    # The next day's fetch covers the same days again: nothing is mailed twice.
    serve(monkeypatch, anthropic_api())
    await run_child(session_factory, gateway_settings, payload(source_id))
    assert len(await _notices(session_factory)) == 4


@pytest.mark.parametrize(
    ("billed", "recorded"),
    [
        # Anthropic bills in cents: $0.50 unexplained, then $5 of $100 (5 %).
        pytest.param("50", Decimal(0), id="under-one-dollar"),
        pytest.param("10000", Decimal(95), id="under-ten-percent"),
    ],
)
async def test_residual_under_either_threshold_notifies_nobody(
    source_id: uuid.UUID,
    owner: Owner,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    billed: str,
    recorded: Decimal,
) -> None:
    if recorded:
        await _spent(session_factory, owner.project_id, recorded)
    serve(monkeypatch, _one_line_api(billed))
    await run_child(session_factory, gateway_settings, payload(source_id))

    assert await _notices(session_factory) == []


@pytest.mark.parametrize("failure", ["slow", "raising"])
async def test_a_slow_or_broken_bypass_check_never_fails_the_stored_fetch(
    source_id: uuid.UUID,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """The fetch is stored before the check runs; a retry would call the provider again."""

    async def slow(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(5)

    async def raising(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(bypass_alert, "build_report", slow if failure == "slow" else raising)
    # The time the check may run to, from the job's start: enough for the rollup refresh before
    # it, so the slow check reaches its timeout rather than being skipped.
    monkeypatch.setattr(bypass_alert, "HARD_STOP_S", 2.0)
    api = anthropic_api()
    serve(monkeypatch, api)
    outcome = "timeout" if failure == "slow" else "failed"
    counted = metric_delta(BILLING_BYPASS_CHECKS, outcome=outcome)

    job = await run_child(session_factory, gateway_settings, payload(source_id))

    assert job.status is JobStatus.DONE, job.last_error
    [run] = await runs(session_factory, source_id)
    assert run["status"] == "ok"
    assert len(api.requests) == 2  # one cost page and one usage page: no second fetch
    assert await _notices(session_factory) == []
    assert counted.value == 1


async def test_untraced_spend_within_retention_notifies_admins(
    source_id: uuid.UUID,
    owner: Owner,
    session_factory: SessionFactory,
    gateway_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bill for models no mapped project called is not explained away as untraced traffic
    (RD17): with the spans still retained, the whole bill is the residual and admins are told."""
    async with session_factory() as db:
        await bypass_rls(db)
        await db.execute(
            text("UPDATE projects SET retention_days = 90 WHERE id = :id"),
            {"id": owner.project_id},
        )
        await db.commit()
    serve(monkeypatch, anthropic_api())  # about $4.36 and $3.40 billed; Spanlight recorded nothing

    await run_child(session_factory, gateway_settings, payload(source_id))

    notices = await _notices(session_factory)
    assert {(n["recipient"], n["summary"]["day"]) for n in notices} == {
        (OWNER, DAY_ONE),
        (ADMIN, DAY_ONE),
        (OWNER, "2026-10-02"),
        (ADMIN, "2026-10-02"),
    }
    first = next(n for n in notices if n["summary"]["day"] == DAY_ONE)
    assert Decimal(first["summary"]["residual_usd"]) > Decimal(4)
    assert "known causes explain $0.00" in first["body"]


def test_bypass_dedupe_outlives_the_refetch_window() -> None:
    """A notice is deduplicated on its outbox row (`billing_bypass:<source>:<day>`), so the row
    must still exist when the last refetch of that day runs: a day is refetched for
    `REFETCH_DAYS` after it closes, and settled rows are kept for `FINISHED_OUTBOX_RETENTION`."""
    assert timedelta(days=REFETCH_DAYS) <= FINISHED_OUTBOX_RETENTION


def test_unknown_or_small_residual_is_not_a_bypass() -> None:
    assert bypass_suspected(provider_usd=Decimal(100), residual_usd=None) is False
    assert bypass_suspected(provider_usd=None, residual_usd=Decimal(50)) is False
    assert bypass_suspected(provider_usd=Decimal(100), residual_usd=Decimal(10)) is False
    assert bypass_suspected(provider_usd=Decimal(5), residual_usd=Decimal("0.99")) is False
    assert bypass_suspected(provider_usd=Decimal(100), residual_usd=Decimal("10.01")) is True
    assert bypass_suspected(provider_usd=Decimal(5), residual_usd=Decimal("1.01")) is True

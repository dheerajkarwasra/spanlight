"""A logical operation never becomes new work merely because its response is missing."""

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import ProblemError
from app.core.idempotency import request_fingerprint
from app.core.idempotency_store import (
    Reservation,
    StoredResponse,
    complete,
    reserve,
)
from app.db.rls import bind_project, bypass_rls
from app.ingest.pipeline import IngestTarget, ingest_spans
from app.main import create_app
from tests.conftest import TEST_ORIGIN
from tests.core.operation_recovery_support import (
    recording_http_provider as http_provider_fixture,
)
from tests.core.operation_recovery_support import (
    response_cache_fault as database_fault_fixture,
)
from tests.core.test_idempotency_replays import export_owner as export_browser
from tests.helpers import Browser, create_workspace, span

export_owner = export_browser
response_cache_fault = database_fault_fixture
recording_http_provider = http_provider_fixture


async def test_an_old_unanswered_operation_is_unknown_not_permission_to_repeat(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Restoring timed takeover would allow a second paid provider invocation."""
    first = await reserve(session_factory, "user:recovery", "external-call", b"a" * 32)
    assert isinstance(first, Reservation)
    async with session_factory() as db:
        await db.execute(
            text("UPDATE idempotency_keys SET created_at = now() - interval '61 seconds'")
        )
        await db.commit()

    with pytest.raises(ProblemError) as refused:
        await reserve(session_factory, "user:recovery", "external-call", b"a" * 32)
    assert refused.value.code == "IDEMPOTENCY_OUTCOME_UNKNOWN"


async def test_completed_operation_can_be_reused_after_response_retention(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Known completed outcomes preserve the documented 24-hour key reuse contract."""
    await reserve(session_factory, "user:recovery", "expired-call", b"a" * 32)
    async with session_factory() as db:
        await db.execute(
            text(
                "UPDATE idempotency_keys SET created_at = now() - interval '2 days', "
                "expires_at = now() - interval '1 day', status = 200, "
                "body = jsonb_build_object('done', true)"
            )
        )
        await db.commit()

    renewed = await reserve(session_factory, "user:recovery", "expired-call", b"b" * 32)
    assert isinstance(renewed, Reservation)


async def test_export_business_commit_survives_response_store_failure(
    export_owner: Browser, session_factory: async_sessionmaker[AsyncSession], response_cache_fault
) -> None:
    """A real database fault rejects middleware writes after the export transaction commits."""
    workspace = await create_workspace(export_owner)
    now = datetime.now(UTC)
    url = f"/api/v1/projects/{workspace.project_id}/exports"
    body = {
        "format": "jsonl",
        "filters": {"from": (now - timedelta(days=1)).isoformat(), "to": now.isoformat()},
    }
    first = await export_owner.post(url, json=body, headers={"Idempotency-Key": "export"})
    assert first.status_code == 202, first.text
    second = await export_owner.post(url, json=body, headers={"Idempotency-Key": "export"})
    assert second.status_code == 202, second.text
    assert second.json()["id"] == first.json()["id"]
    async with session_factory() as db:
        await bypass_rls(db)
        assert await db.scalar(text("SELECT count(*) FROM exports")) == 1


async def test_live_provider_past_sixty_seconds_is_not_called_by_a_second_replica(
    recording_http_provider, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Real elapsed lease time and an independent DB session cannot authorize a paid retry."""
    url, entered, gate, requests = recording_http_provider
    reservation = await reserve(session_factory, "user:live", "paid", b"a" * 32)
    assert isinstance(reservation, Reservation)
    async with httpx.AsyncClient(timeout=90) as client:
        pending = asyncio.create_task(client.get(url))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            await asyncio.sleep(61)
            replica_sessions = async_sessionmaker(
                session_factory.kw["bind"], expire_on_commit=False
            )
            with pytest.raises(ProblemError) as refused:
                await reserve(replica_sessions, "user:live", "paid", b"a" * 32)
            assert refused.value.code == "IDEMPOTENCY_OUTCOME_UNKNOWN"
            assert len(requests) == 1
        finally:
            gate.set()
            response = await pending
        assert response.status_code == 200
    assert await complete(
        session_factory, reservation, StoredResponse(200, {"paid": True}, "application/json")
    )
    recovered = await reserve(session_factory, "user:live", "paid", b"a" * 32)
    assert isinstance(recovered, StoredResponse)
    assert recovered.body == {"paid": True}
    assert len(requests) == 1


async def test_process_death_after_http_success_never_repeats_the_external_operation(
    recording_http_provider, session_factory: async_sessionmaker[AsyncSession], settings
) -> None:
    """The first process exits after a real HTTP success, before persisting any answer."""
    url, entered, gate, requests = recording_http_provider
    gate.set()
    code = """
import asyncio, os, sys
import httpx
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.idempotency_store import reserve
async def run():
    engine = create_async_engine(sys.argv[1])
    await reserve(async_sessionmaker(engine), 'user:crashed', 'paid', b'a' * 32)
    async with httpx.AsyncClient() as client:
        response = await client.get(sys.argv[2])
        assert response.status_code == 200
    os._exit(23)
asyncio.run(run())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        str(settings.database_url),
        url,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
    assert process.returncode == 23, stderr.decode()
    assert entered.is_set()
    assert len(requests) == 1
    async with session_factory() as db:
        await db.execute(
            text(
                "UPDATE idempotency_keys SET created_at = now() - interval '2 days', "
                "expires_at = now() - interval '1 day'"
            )
        )
        await db.commit()
    with pytest.raises(ProblemError) as refused:
        await reserve(session_factory, "user:crashed", "paid", b"a" * 32)
    assert refused.value.code == "IDEMPOTENCY_OUTCOME_UNKNOWN"
    assert len(requests) == 1


@given(
    st.dictionaries(
        st.text(max_size=20),
        st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=30)),
        max_size=8,
    )
)
def test_request_identity_is_stable_under_json_layout_changes(body: dict) -> None:
    compact = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    spaced = json.dumps(dict(reversed(list(body.items()))), indent=2).encode()
    assert request_fingerprint("POST", "/operation", compact) == request_fingerprint(
        "POST", "/operation", spaced
    )
    assert request_fingerprint("POST", "/operation", compact) != request_fingerprint(
        "POST", "/other-operation", spaced
    )


async def test_replay_budget_refusal_commits_its_receipt_with_the_failed_ledger(
    gateway_workspace,
    gateway_settings,
    session_factory,
    response_cache_fault,
) -> None:
    gateway_settings.replay_monthly_budget_usd = Decimal("0.00000001")
    source = span(
        trace_id="1" * 32,
        span_id="2" * 16,
        model="gpt-4.1-mini",
        input={"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "Hello"}]},
    )
    async with session_factory() as db:
        await bind_project(db, gateway_workspace.project_id)
        result = await ingest_spans(
            db,
            IngestTarget(
                project_id=gateway_workspace.project_id,
                org_id=gateway_workspace.org_id,
                capture_payloads=True,
            ),
            [source],
        )
        assert result.accepted == 1
        await db.commit()
    url = f"/api/v1/projects/{gateway_workspace.project_id}/playground/replays"
    body = {
        "trace_id": "1" * 32,
        "span_id": "2" * 16,
        "credential_id": str(gateway_workspace.openai_credential_id),
    }
    first = await gateway_workspace.owner.post(
        url, json=body, headers={"Idempotency-Key": "refused-replay"}
    )
    assert first.status_code == 409, first.text
    assert first.json()["code"] == "REPLAY_BUDGET_EXCEEDED", first.text
    second = await gateway_workspace.owner.post(
        url, json=body, headers={"Idempotency-Key": "refused-replay"}
    )
    assert second.status_code == 409, second.text
    assert second.json() == first.json()
    assert second.headers["Idempotent-Replayed"] == "true"
    async with session_factory() as db:
        await bind_project(db, gateway_workspace.project_id)
        assert await db.scalar(text("SELECT count(*) FROM replay_runs")) == 1


async def test_one_time_channel_secret_is_never_saved_as_an_operation_receipt(
    gateway_workspace, session_factory
) -> None:
    response = await gateway_workspace.owner.post(
        f"/api/v1/orgs/{gateway_workspace.org_id}/alert-channels",
        json={
            "kind": "webhook",
            "name": "one-time",
            "config": {"url": "https://hooks.example.com/test"},
        },
        headers={"Idempotency-Key": "must-not-cache-secret"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["secret"]
    async with session_factory() as db:
        assert await db.scalar(text("SELECT count(*) FROM idempotency_keys")) == 0


async def test_a_second_api_replica_recovers_the_committed_export(
    export_owner: Browser, settings, session_factory, response_cache_fault
) -> None:
    workspace = await create_workspace(export_owner)
    now = datetime.now(UTC)
    url = f"/api/v1/projects/{workspace.project_id}/exports"
    body = {
        "format": "jsonl",
        "filters": {"from": (now - timedelta(days=1)).isoformat(), "to": now.isoformat()},
    }
    first = await export_owner.post(url, json=body, headers={"Idempotency-Key": "replica-export"})
    assert first.status_code == 202, first.text
    replica = create_app(settings)
    async with (
        replica.router.lifespan_context(replica),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=replica),
            base_url=TEST_ORIGIN,
            headers={"Origin": TEST_ORIGIN},
            cookies=export_owner.http.cookies,
        ) as http,
    ):
        browser = Browser(http=http, user=export_owner.user)
        second = await browser.post(url, json=body, headers={"Idempotency-Key": "replica-export"})
    assert second.status_code == 202, second.text
    assert second.json() == first.json()
    assert second.headers["Idempotent-Replayed"] == "true"
    async with session_factory() as db:
        await bypass_rls(db)
        assert await db.scalar(text("SELECT count(*) FROM exports")) == 1

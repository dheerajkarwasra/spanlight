"""Real provider responses cannot erase ambiguity or abort domain settlement via receipts."""

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from app.db.rls import bind_project
from app.jobs.tasks.cleanup import cleanup
from tests.core.external_operation_support import (
    http_model_provider as provider_fixture,
)
from tests.core.external_operation_support import (
    provider_workspace as workspace_fixture,
)
from tests.core.external_operation_support import (
    real_provider_app as application_fixture,
)

http_model_provider = provider_fixture
provider_workspace = workspace_fixture
app = application_fixture
KEY = {"Idempotency-Key": "external-receipt"}


def request_body(workspace, route: str) -> dict:
    if route == "replays":
        return {"trace_id": "3" * 32, "span_id": "4" * 16, "credential_id": workspace.credential_id}
    return {
        "configs": [
            {
                "credential_id": workspace.credential_id,
                "model": "gpt-4.1-mini",
                "source": {"messages": [{"role": "user", "content": "Hello"}]},
            }
        ]
    }


async def age_and_clean(session_factory) -> None:
    async with session_factory() as db:
        await db.execute(
            text(
                "UPDATE idempotency_keys SET created_at = now() - interval '2 days', "
                "expires_at = now() - interval '1 day'"
            )
        )
        await db.commit()
    await cleanup(session_factory, now=datetime.now(UTC))


@pytest.mark.parametrize("route", ["runs", "replays"])
@pytest.mark.parametrize("failure", ["cancel", "disconnect"])
async def test_unanswered_call_remains_unknown_after_retention_and_cleanup(
    provider_workspace, session_factory, failure: str, route: str
) -> None:
    workspace = provider_workspace
    provider = workspace.provider
    body = request_body(workspace, route)
    url = f"{workspace.base}/{route}"
    provider.disconnect = failure == "disconnect"
    pending = asyncio.create_task(workspace.owner.post(url, json=body, headers=KEY))
    await asyncio.wait_for(provider.entered.wait(), timeout=10)
    if failure == "cancel":
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        provider.gate.set()
    else:
        provider.gate.set()
        first = await pending
        assert first.status_code == 200, first.text
        result = first.json()["results"][0] if route == "runs" else first.json()
        assert result["error"] is not None
    if route == "replays":
        async with session_factory() as db:
            await bind_project(db, workspace.project_id)
            ledger = (await db.execute(text("SELECT status, cost_usd FROM replay_runs"))).one()
            assert ledger.status == "failed"
            assert ledger.cost_usd > 0
    await age_and_clean(session_factory)
    retry = await workspace.owner.post(url, json=body, headers=KEY)
    assert retry.status_code == 409, retry.text
    assert retry.json()["code"] == "IDEMPOTENCY_OUTCOME_UNKNOWN"
    assert len(provider.requests) == 1


@pytest.mark.parametrize("route", ["runs", "replays"])
@pytest.mark.parametrize(
    "output", ["hello\x00world", "x" * (1024 * 1024)], ids=["nul", "oversized"]
)
async def test_unstorable_provider_output_keeps_response_and_domain_settlement(
    provider_workspace, session_factory, route: str, output: str
) -> None:
    workspace = provider_workspace
    workspace.provider.output = output
    workspace.provider.gate.set()
    url = f"{workspace.base}/{route}"
    body = request_body(workspace, route)
    first = await workspace.owner.post(url, json=body, headers=KEY)
    assert first.status_code == 200, first.text[:500]
    result = first.json()["results"][0] if route == "runs" else first.json()
    assert result["output"] == output
    if route == "replays":
        async with session_factory() as db:
            await bind_project(db, workspace.project_id)
            ledger = (await db.execute(text("SELECT status, cost_usd FROM replay_runs"))).one()
            assert ledger.status == "done"
            assert ledger.cost_usd > 0
    async with session_factory() as db:
        operation = (await db.execute(text("SELECT status, body FROM idempotency_keys"))).one()
        assert operation.status is None
        assert operation.body is None
    await age_and_clean(session_factory)
    retry = await workspace.owner.post(url, json=body, headers=KEY)
    assert retry.status_code == 409, retry.text[:500]
    assert retry.json()["code"] == "IDEMPOTENCY_OUTCOME_UNKNOWN"
    assert len(workspace.provider.requests) == 1

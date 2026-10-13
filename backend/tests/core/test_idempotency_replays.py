"""`Idempotency-Key` on the three routes that spend money or queue a job: a retry gets the first
answer back and nothing runs twice.

- A trace export: the replay names the same export, and one export and one job exist.
- An experiment run: the replay names the same run; the one queued job, when the worker runs it,
  calls the provider once (the fake provider of `tests/gateway_support.py`).
- A playground run: the replay returns the stored response and the provider got one request.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.idempotency import REPLAYED_HEADER
from app.db.rls import bypass_rls
from app.evals.experiment_run import execute_run
from app.evals.runner_build import RunnerDeps
from app.gateway.runtime import GatewayRuntime
from app.jobs.queue import ClaimedJob, claim_next
from app.main import create_app
from tests.conftest import TEST_ORIGIN
from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import FakeProvider, fixture_text
from tests.helpers import Browser, create_workspace

KEY = {"Idempotency-Key": "retry-me-1"}
SessionFactory = async_sessionmaker[AsyncSession]
CHAT_MESSAGES = [{"role": "user", "content": "Hello"}]


async def _count(session_factory: SessionFactory, sql: str, **params: Any) -> int:
    async with session_factory() as db:
        await bypass_rls(db)
        return int((await db.execute(text(sql), params)).scalar_one())


def _assert_replay(first: httpx.Response, second: httpx.Response, status: int) -> None:
    assert first.status_code == second.status_code == status, (first.text, second.text)
    assert REPLAYED_HEADER not in first.headers
    assert second.headers[REPLAYED_HEADER] == "true"
    assert second.json() == first.json()


# --- exports -----------------------------------------------------------------------------------


@pytest.fixture
async def export_owner(settings: Settings) -> AsyncIterator[Browser]:
    """An org owner on an app with object storage configured (nothing connects to it)."""
    configured = settings.model_copy(
        update={
            "s3_bucket": "exports-test",
            "s3_access_key": SecretStr("test-access"),
            "s3_secret_key": SecretStr("test-secret"),
            "s3_endpoint": "http://localhost:9",
            "s3_force_path_style": True,
        }
    )
    application = create_app(configured)
    async with application.router.lifespan_context(application):
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application),
            base_url=TEST_ORIGIN,
            headers={"Origin": TEST_ORIGIN},
        )
        signup = await http.post(
            "/api/v1/auth/signup",
            json={"email": "owner@example.com", "password": "correct horse battery", "name": "O"},
        )
        assert signup.status_code == 201, signup.text
        try:
            yield Browser(http=http, user=signup.json())
        finally:
            await http.aclose()


async def test_export_replay_returns_the_same_export_id(
    export_owner: Browser, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(export_owner)
    now = datetime.now(UTC)
    url = f"/api/v1/projects/{workspace.project_id}/exports"
    body = {
        "format": "jsonl",
        "filters": {"from": (now - timedelta(days=1)).isoformat(), "to": now.isoformat()},
    }

    first = await export_owner.post(url, json=body, headers=KEY)
    second = await export_owner.post(url, json=body, headers=KEY)

    _assert_replay(first, second, 202)
    export_id = first.json()["id"]
    assert uuid.UUID(export_id)
    assert await _count(session_factory, "SELECT count(*) FROM exports") == 1
    assert (
        await _count(session_factory, "SELECT count(*) FROM jobs WHERE kind = 'create_export'") == 1
    )
    listed = await export_owner.get(url)
    assert [item["id"] for item in listed.json()["items"]] == [export_id]


# --- experiment runs ---------------------------------------------------------------------------


async def _experiment(workspace: GatewayWorkspace) -> str:
    """A one-item dataset and an experiment on the OpenAI credential; returns its runs URL."""
    base = f"/api/v1/projects/{workspace.project_id}"
    owner = workspace.owner
    dataset = await owner.post(f"{base}/datasets", json={"name": "replay-set"})
    assert dataset.status_code == 201, dataset.text
    dataset_id = dataset.json()["id"]
    items = await owner.post(
        f"{base}/datasets/{dataset_id}/items", json={"items": [{"input": {"q": "Hello"}}]}
    )
    assert items.status_code == 201, items.text
    target = {
        "kind": "messages",
        "credential_id": str(workspace.openai_credential_id),
        "model": "gpt-4.1-mini",
        "messages": CHAT_MESSAGES,
    }
    experiment = await owner.post(
        f"{base}/experiments",
        json={"name": "replay", "dataset_id": dataset_id, "config": {"target": target}},
    )
    assert experiment.status_code == 201, experiment.text
    return f"{base}/experiments/{experiment.json()['id']}/runs"


async def test_experiment_run_replay_starts_one_run(
    gateway_app: Any,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: SessionFactory,
    gateway_settings: Settings,
) -> None:
    url = await _experiment(gateway_workspace)
    owner = gateway_workspace.owner

    first = await owner.post(url, headers=KEY)
    second = await owner.post(url, headers=KEY)

    _assert_replay(first, second, 202)
    run_id = first.json()["id"]
    assert await _count(session_factory, "SELECT count(*) FROM experiment_runs") == 1
    assert (
        await _count(session_factory, "SELECT count(*) FROM jobs WHERE kind = 'run_experiment'")
        == 1
    )
    # The worker takes the one job; the dataset has one item, so the provider is called once.
    fake_provider.reply_json(fixture_text("openai/chat.json").encode())
    async with session_factory() as db:
        job = await claim_next(db)
    assert isinstance(job, ClaimedJob)
    assert job.payload["run_id"] == run_id
    runtime: GatewayRuntime = gateway_app.state.gateway_runtime
    deps = RunnerDeps(sessions=session_factory, settings=gateway_settings, services=runtime)
    await execute_run(deps, uuid.UUID(run_id), gateway_workspace.project_id)
    assert len(fake_provider.requests) == 1
    finished = await owner.get(f"{url}/{run_id}")
    assert finished.json()["status"] == "done", finished.text
    # A later retry with the same key still gets the first answer, and starts nothing new.
    third = await owner.post(url, headers=KEY)
    _assert_replay(first, third, 202)
    assert await _count(session_factory, "SELECT count(*) FROM experiment_runs") == 1
    assert len(fake_provider.requests) == 1


# --- playground --------------------------------------------------------------------------------


async def test_playground_replay_returns_the_stored_response(
    gateway_workspace: GatewayWorkspace, fake_provider: FakeProvider
) -> None:
    fake_provider.reply_json(fixture_text("openai/chat.json").encode())
    url = f"/api/v1/projects/{gateway_workspace.project_id}/playground/runs"
    body = {
        "configs": [
            {
                "credential_id": str(gateway_workspace.openai_credential_id),
                "model": "gpt-4.1-mini",
                "source": {"messages": CHAT_MESSAGES},
            }
        ]
    }

    first = await gateway_workspace.owner.post(url, json=body, headers=KEY)
    second = await gateway_workspace.owner.post(url, json=body, headers=KEY)

    _assert_replay(first, second, 200)
    [result] = second.json()["results"]
    assert result["error"] is None
    assert result["output"]
    assert result["trace_id"] == first.json()["results"][0]["trace_id"]
    assert len(fake_provider.requests) == 1

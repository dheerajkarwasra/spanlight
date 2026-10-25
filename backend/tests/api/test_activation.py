"""The setup checklist: four steps read from rows, and a per-user dismissal (UX Phase 6, §5.1)."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bypass_rls
from tests.conftest import BrowserFactory, _make_client
from tests.evals.runs import insert_run
from tests.helpers import (
    Browser,
    Workspace,
    bearer,
    create_api_key,
    create_token,
    create_workspace,
    new_trace_id,
    span,
)

DISMISS = "/api/v1/auth/me/activation-checklist"
NOTHING_DONE = {"first_trace": False, "session": False, "budget": False, "evaluation": False}


def activation_url(workspace: Workspace) -> str:
    return f"/api/v1/projects/{workspace.project_id}/activation"


async def activation(workspace: Workspace) -> dict[str, Any]:
    response = await workspace.owner.get(activation_url(workspace))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def send_trace(client: httpx.AsyncClient, workspace: Workspace, **trace: Any) -> str:
    trace_id = new_trace_id()
    body = span(trace_id=trace_id, trace={"name": "answer", **trace})
    response = await client.post(
        "/v1/traces", json={"spans": [body]}, headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200, response.text
    return trace_id


async def dismissed(browser: Browser) -> bool:
    response = await browser.get("/api/v1/auth/me")
    assert response.status_code == 200, response.text
    value: bool = response.json()["activation_checklist_dismissed"]
    return value


async def dismissed_at(session_factory: async_sessionmaker[AsyncSession], browser: Browser) -> Any:
    async with session_factory() as db:
        return await db.scalar(
            text("SELECT activation_checklist_dismissed_at FROM users WHERE id = :id"),
            {"id": uuid.UUID(browser.user["id"])},
        )


@pytest.fixture
async def workspace(browser_factory: BrowserFactory) -> Workspace:
    return await create_workspace(await browser_factory("ada@example.com"))


@pytest.fixture
async def demo(app: Any) -> AsyncIterator[Browser]:
    async with _make_client(app) as http:
        response = await http.post("/api/v1/demo/session")
        assert response.status_code == 200, response.text
        yield Browser(http=http, user=response.json())


async def test_a_new_project_has_no_step_done(workspace: Workspace) -> None:
    assert await activation(workspace) == NOTHING_DONE


async def test_the_setup_test_trace_is_not_a_first_trace(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    await send_trace(client, workspace, tags=["spanlight:setup-test"])
    assert (await activation(workspace))["first_trace"] is False

    await send_trace(client, workspace, tags=["beta"])
    assert (await activation(workspace))["first_trace"] is True


async def test_an_untagged_trace_is_a_first_trace(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    await send_trace(client, workspace)
    assert await activation(workspace) == {**NOTHING_DONE, "first_trace": True}


async def test_a_trace_with_a_session_id_completes_the_session_step(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    await send_trace(client, workspace, session_id="chat-1", tags=["spanlight:setup-test"])
    assert await activation(workspace) == {**NOTHING_DONE, "session": True}


async def test_a_budget_completes_the_budget_step(workspace: Workspace) -> None:
    created = await workspace.owner.post(
        f"/api/v1/projects/{workspace.project_id}/budgets",
        json={
            "name": "Monthly cap",
            "scope": "project",
            "period": "monthly",
            "amount_usd": "50",
            "action": "notify",
            "channel_ids": [],
        },
    )
    assert created.status_code == 201, created.text
    assert await activation(workspace) == {**NOTHING_DONE, "budget": True}


async def test_an_api_score_is_not_an_evaluation_but_a_human_score_is(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    trace_id = await send_trace(client, workspace, tags=["spanlight:setup-test"])
    key = await create_api_key(workspace.owner, workspace.project_id, scopes=["scores:write"])
    sent = await client.post(
        "/v1/scores",
        json={"scores": [{"trace_id": trace_id, "name": "tone", "value": 1}]},
        headers=bearer(key["secret"]),
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["rejected"] == []
    assert (await activation(workspace))["evaluation"] is False

    human = await workspace.owner.post(
        f"/api/v1/projects/{workspace.project_id}/traces/{trace_id}/scores",
        json={"name": "tone", "value": 0},
    )
    assert human.status_code == 201, human.text
    assert (await activation(workspace))["evaluation"] is True


async def test_an_eval_score_is_an_evaluation(
    session_factory: async_sessionmaker[AsyncSession], workspace: Workspace
) -> None:
    project_id = uuid.UUID(workspace.project_id)
    evaluator_id = uuid.uuid4()
    async with session_factory() as db:
        await bypass_rls(db)
        await db.execute(
            text(
                "INSERT INTO evaluators (id, project_id, name, kind, config, monthly_budget_usd) "
                "VALUES (:id, :project_id, 'judge', 'llm_judge', '{\"kind\": \"llm_judge\"}', 1)"
            ),
            {"id": evaluator_id, "project_id": project_id},
        )
        await db.commit()
    run_id = await insert_run(session_factory, evaluator_id, started_at=datetime.now(UTC))
    async with session_factory() as db:
        await bypass_rls(db)
        await db.execute(
            text(
                "INSERT INTO scores (id, project_id, trace_id, name, data_type, value, source, "
                "evaluator_run_id) VALUES (:id, :project_id, :trace_id, 'tone', 'numeric', 1, "
                "'eval', :run_id)"
            ),
            {
                "id": uuid.uuid4(),
                "project_id": project_id,
                "trace_id": new_trace_id(),
                "run_id": run_id,
            },
        )
        await db.commit()
    assert await activation(workspace) == {**NOTHING_DONE, "evaluation": True}


async def test_orphaned_scores_and_another_projects_scores_are_not_an_evaluation(
    session_factory: async_sessionmaker[AsyncSession],
    browser_factory: BrowserFactory,
    workspace: Workspace,
) -> None:
    # A human score whose author was deleted, an eval score whose run was pruned (both SET NULL),
    # and a real human score in someone else's project.
    other = await create_workspace(await browser_factory("grace@example.com"))
    grace_id = uuid.UUID((await other.owner.get("/api/v1/auth/me")).json()["user"]["id"])
    async with session_factory() as db:
        await bypass_rls(db)
        for project_id, source, author in (
            (workspace.project_id, "human", None),
            (workspace.project_id, "eval", None),
            (other.project_id, "human", grace_id),
        ):
            await db.execute(
                text(
                    "INSERT INTO scores (id, project_id, trace_id, name, data_type, value, "
                    "source, author_user_id) VALUES (:id, :project_id, :trace_id, 'tone', "
                    "'numeric', 1, CAST(:source AS score_source), :author)"
                ),
                {
                    "id": uuid.uuid4(),
                    "project_id": uuid.UUID(project_id),
                    "trace_id": new_trace_id(),
                    "source": source,
                    "author": author,
                },
            )
        await db.commit()

    assert await activation(workspace) == NOTHING_DONE
    assert (await activation(other))["evaluation"] is True


async def test_another_tenants_project_is_a_404(
    browser_factory: BrowserFactory, workspace: Workspace
) -> None:
    mallory = await browser_factory("mallory@example.com")
    response = await mallory.get(activation_url(workspace))
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def test_dismissing_shows_on_me_and_showing_again_clears_it(
    workspace: Workspace, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    ada = workspace.owner
    assert await dismissed(ada) is False

    first = await ada.put(DISMISS, json={"dismissed": True})
    assert first.status_code == 204, first.text
    assert await dismissed(ada) is True
    stamped = await dismissed_at(session_factory, ada)
    assert stamped is not None

    # Idempotent: dismissing again keeps the moment it was first hidden.
    again = await ada.put(DISMISS, json={"dismissed": True})
    assert again.status_code == 204, again.text
    assert await dismissed_at(session_factory, ada) == stamped

    shown = await ada.put(DISMISS, json={"dismissed": False})
    assert shown.status_code == 204, shown.text
    assert await dismissed(ada) is False
    assert await dismissed_at(session_factory, ada) is None


async def test_dismissal_is_the_callers_own(browser_factory: BrowserFactory) -> None:
    ada = await browser_factory("ada@example.com")
    bob = await browser_factory("bob@example.com")
    assert (await ada.put(DISMISS, json={"dismissed": True})).status_code == 204
    assert await dismissed(ada) is True
    assert await dismissed(bob) is False


async def test_the_body_must_say_dismissed(workspace: Workspace) -> None:
    for body in ({}, {"dismissed": "maybe"}):
        response = await workspace.owner.put(DISMISS, json=body)
        assert response.status_code == 422, response.text


async def test_a_write_token_dismisses_and_a_read_token_is_refused(
    bare_client: httpx.AsyncClient, workspace: Workspace
) -> None:
    write = (await create_token(workspace.owner, scope="write"))["token"]
    read = (await create_token(workspace.owner, scope="read"))["token"]

    refused = await bare_client.put(DISMISS, json={"dismissed": True}, headers=bearer(read))
    assert refused.status_code == 403
    assert refused.json()["code"] == "TOKEN_SCOPE"
    assert await dismissed(workspace.owner) is False

    done = await bare_client.put(DISMISS, json={"dismissed": True}, headers=bearer(write))
    assert done.status_code == 204, done.text
    assert await dismissed(workspace.owner) is True


async def test_the_demo_account_cannot_hide_the_checklist(demo: Browser) -> None:
    response = await demo.put(DISMISS, json={"dismissed": True})
    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    assert response.json()["detail"] == "The demo account can't hide the setup checklist."
    assert await dismissed(demo) is False


async def test_dismissing_needs_the_csrf_token(workspace: Workspace) -> None:
    response = await workspace.owner.http.put(DISMISS, json={"dismissed": True})
    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_FAILED"
    assert await dismissed(workspace.owner) is False


async def test_dismissing_from_a_foreign_origin_is_refused(workspace: Workspace) -> None:
    response = await workspace.owner.http.put(
        DISMISS,
        json={"dismissed": True},
        headers={
            "X-CSRF-Token": workspace.owner.http.cookies["spl_csrf"],
            "Origin": "https://evil.example",
        },
    )
    assert response.status_code == 403
    assert response.json()["code"] == "ORIGIN_NOT_ALLOWED"
    assert await dismissed(workspace.owner) is False

"""API key scopes and expiry: what a key may do, and when it stops working."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.helpers import (
    Browser,
    Workspace,
    bearer,
    create_api_key,
    create_workspace,
    expire_api_key,
    new_trace_id,
    span,
)

SessionFactory = async_sessionmaker[AsyncSession]

INGEST_ROUTES = [
    pytest.param("/v1/traces", {"spans": []}, id="native"),
    pytest.param("/v1/otlp/traces", {"resourceSpans": []}, id="otlp"),
]
FUTURE = (datetime.now(UTC) + timedelta(days=30)).isoformat()


@pytest.fixture
async def workspace(browser_factory: Any) -> Workspace:
    owner: Browser = await browser_factory("owner@example.com")
    return await create_workspace(owner)


def _traces_url(workspace: Workspace) -> str:
    return f"/api/v1/projects/{workspace.project_id}/traces"


async def test_a_key_created_without_scopes_may_ingest(workspace: Workspace) -> None:
    created = await create_api_key(workspace.owner, workspace.project_id)

    assert created["scopes"] == ["ingest:write"]
    assert created["expires_at"] is None
    listed = (await workspace.owner.get(f"/api/v1/projects/{workspace.project_id}/keys")).json()
    assert {key["id"]: key["scopes"] for key in listed}[created["id"]] == ["ingest:write"]


@pytest.mark.parametrize(("path", "body"), INGEST_ROUTES)
async def test_a_default_key_ingests(
    client: httpx.AsyncClient, workspace: Workspace, path: str, body: dict[str, Any]
) -> None:
    response = await client.post(path, json=body, headers=bearer(workspace.api_key))
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(("path", "body"), INGEST_ROUTES)
async def test_a_read_only_key_cannot_ingest(
    client: httpx.AsyncClient, workspace: Workspace, path: str, body: dict[str, Any]
) -> None:
    created = await create_api_key(workspace.owner, workspace.project_id, scopes=["traces:read"])

    response = await client.post(path, json=body, headers=bearer(created["secret"]))

    assert response.status_code == 403
    assert response.json()["code"] == "KEY_SCOPE"


async def test_a_refused_ingest_stores_nothing(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    reader = await create_api_key(workspace.owner, workspace.project_id, scopes=["traces:read"])
    batch = {"spans": [span(trace_id=new_trace_id())]}

    refused = await client.post("/v1/traces", json=batch, headers=bearer(reader["secret"]))

    assert refused.status_code == 403
    traces = await workspace.owner.get(_traces_url(workspace))
    assert traces.json()["items"] == []


@pytest.mark.parametrize(("path", "body"), INGEST_ROUTES)
async def test_a_key_with_both_scopes_ingests(
    client: httpx.AsyncClient, workspace: Workspace, path: str, body: dict[str, Any]
) -> None:
    created = await create_api_key(
        workspace.owner, workspace.project_id, scopes=["ingest:write", "traces:read"]
    )

    response = await client.post(path, json=body, headers=bearer(created["secret"]))

    assert response.status_code == 200, response.text


@pytest.mark.parametrize(("path", "body"), INGEST_ROUTES)
async def test_an_expired_key_is_key_expired_on_ingest(
    client: httpx.AsyncClient,
    workspace: Workspace,
    session_factory: SessionFactory,
    path: str,
    body: dict[str, Any],
) -> None:
    created = await create_api_key(workspace.owner, workspace.project_id, expires_at=FUTURE)
    ok = await client.post(path, json=body, headers=bearer(created["secret"]))
    assert ok.status_code == 200, ok.text

    await expire_api_key(session_factory, created["id"])
    response = await client.post(path, json=body, headers=bearer(created["secret"]))

    assert response.status_code == 401
    assert response.json()["code"] == "KEY_EXPIRED"


async def test_an_expired_key_is_key_expired_on_reads(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    created = await create_api_key(
        workspace.owner, workspace.project_id, scopes=["traces:read"], expires_at=FUTURE
    )
    await expire_api_key(session_factory, created["id"])

    response = await client.get(_traces_url(workspace), headers=bearer(created["secret"]))

    assert response.status_code == 401
    assert response.json()["code"] == "KEY_EXPIRED"


async def test_expiry_is_not_revealed_without_the_secret(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    created = await create_api_key(workspace.owner, workspace.project_id, expires_at=FUTURE)
    await expire_api_key(session_factory, created["id"])
    prefix = created["secret"].rsplit("_", 1)[0]

    response = await client.post(
        "/v1/traces", json={"spans": []}, headers=bearer(f"{prefix}_{'a' * 32}")
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_a_revoked_key_stays_unauthorized_even_when_expired(
    client: httpx.AsyncClient, workspace: Workspace, session_factory: SessionFactory
) -> None:
    created = await create_api_key(workspace.owner, workspace.project_id, expires_at=FUTURE)
    keys_url = f"/api/v1/projects/{workspace.project_id}/keys"
    assert (await workspace.owner.delete(f"{keys_url}/{created['id']}")).status_code == 204
    await expire_api_key(session_factory, created["id"])

    response = await client.post(
        "/v1/traces", json={"spans": []}, headers=bearer(created["secret"])
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


async def test_a_key_with_a_future_expiry_works_and_reports_it(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    created = await create_api_key(workspace.owner, workspace.project_id, expires_at=FUTURE)

    assert datetime.fromisoformat(created["expires_at"]) == datetime.fromisoformat(FUTURE)
    response = await client.post(
        "/v1/traces", json={"spans": []}, headers=bearer(created["secret"])
    )
    assert response.status_code == 200
    listed = (await workspace.owner.get(f"/api/v1/projects/{workspace.project_id}/keys")).json()
    assert {key["id"]: key["expires_at"] for key in listed}[created["id"]] == created["expires_at"]


async def test_an_expiry_without_an_offset_is_utc(workspace: Workspace) -> None:
    created = await create_api_key(
        workspace.owner, workspace.project_id, expires_at="2099-01-01T00:00:00"
    )
    assert datetime.fromisoformat(created["expires_at"]) == datetime(2099, 1, 1, tzinfo=UTC)

    shifted = await create_api_key(
        workspace.owner, workspace.project_id, expires_at="2099-01-01T02:00:00+02:00"
    )
    assert datetime.fromisoformat(shifted["expires_at"]) == datetime(2099, 1, 1, tzinfo=UTC)


async def test_a_null_expiry_means_no_expiry(workspace: Workspace) -> None:
    created = await create_api_key(workspace.owner, workspace.project_id, expires_at=None)
    assert created["expires_at"] is None


@pytest.mark.parametrize(
    "expires_at",
    [
        (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        (datetime.now(UTC) - timedelta(days=365)).isoformat(),
        "2000-01-01T00:00:00",
    ],
    ids=["a-minute-ago", "a-year-ago", "naive-2000"],
)
async def test_an_expiry_in_the_past_is_a_validation_error(
    workspace: Workspace, expires_at: str
) -> None:
    response = await workspace.owner.post(
        f"/api/v1/projects/{workspace.project_id}/keys",
        json={"name": "late", "expires_at": expires_at},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert [error["field"] for error in body["errors"]] == ["expires_at"]


@pytest.mark.parametrize("expires_at", ["9999-12-31T23:00:00-02:00", "9999-12-31T23:59:59-00:01"])
async def test_an_expiry_beyond_the_last_representable_time_is_a_validation_error(
    workspace: Workspace, expires_at: str
) -> None:
    """In UTC these are in year 10000, which Python cannot represent: 422, not a server error."""
    response = await workspace.owner.post(
        f"/api/v1/projects/{workspace.project_id}/keys",
        json={"name": "forever", "expires_at": expires_at},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert [error["field"] for error in body["errors"]] == ["expires_at"]


async def test_the_last_representable_expiry_is_accepted(workspace: Workspace) -> None:
    created = await create_api_key(
        workspace.owner, workspace.project_id, expires_at="9999-12-31T23:59:59Z"
    )

    assert datetime.fromisoformat(created["expires_at"]) == datetime(
        9999, 12, 31, 23, 59, 59, tzinfo=UTC
    )


@pytest.mark.parametrize(
    "scopes",
    [["admin"], ["ingest:write", "traces:write"], ["INGEST:WRITE"], [""], "ingest:write", [None]],
)
async def test_an_unknown_scope_is_a_validation_error(workspace: Workspace, scopes: Any) -> None:
    response = await workspace.owner.post(
        f"/api/v1/projects/{workspace.project_id}/keys", json={"name": "bad", "scopes": scopes}
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert response.json()["errors"][0]["field"].startswith("scopes")


async def test_an_empty_scope_list_is_a_validation_error(workspace: Workspace) -> None:
    response = await workspace.owner.post(
        f"/api/v1/projects/{workspace.project_id}/keys", json={"name": "none", "scopes": []}
    )

    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "scopes"


async def test_a_refused_creation_makes_no_key(workspace: Workspace) -> None:
    keys_url = f"/api/v1/projects/{workspace.project_id}/keys"
    before = (await workspace.owner.get(keys_url)).json()

    await workspace.owner.post(keys_url, json={"name": "bad", "scopes": ["nope"]})
    await workspace.owner.post(
        keys_url, json={"name": "late", "expires_at": "2000-01-01T00:00:00Z"}
    )

    assert (await workspace.owner.get(keys_url)).json() == before


async def test_duplicate_scopes_are_stored_once_in_the_order_given(workspace: Workspace) -> None:
    created = await create_api_key(
        workspace.owner,
        workspace.project_id,
        scopes=["traces:read", "ingest:write", "traces:read"],
    )

    assert created["scopes"] == ["traces:read", "ingest:write"]


async def test_the_reserved_scopes_are_accepted(workspace: Workspace) -> None:
    created = await create_api_key(
        workspace.owner, workspace.project_id, scopes=["scores:write", "prompts:read"]
    )

    assert created["scopes"] == ["scores:write", "prompts:read"]


async def test_the_audit_event_records_scopes_and_expiry(workspace: Workspace) -> None:
    created = await create_api_key(
        workspace.owner, workspace.project_id, scopes=["traces:read"], expires_at=FUTURE
    )

    events = (await workspace.owner.get(f"/api/v1/orgs/{workspace.org_id}/audit")).json()
    event = next(
        e
        for e in events["items"]
        if e["action"] == "key.create" and e["target_id"] == created["id"]
    )
    assert event["metadata"]["scopes"] == ["traces:read"]
    assert datetime.fromisoformat(event["metadata"]["expires_at"]) == datetime.fromisoformat(FUTURE)

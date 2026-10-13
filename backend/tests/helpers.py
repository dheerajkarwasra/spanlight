"""Builders shared by tests."""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass
class Browser:
    """A signed-in browser: cookies plus the CSRF header the SPA would send."""

    http: httpx.AsyncClient
    user: dict[str, Any]

    def _headers(self) -> dict[str, str]:
        token = self.http.cookies.get("spl_csrf")
        return {"X-CSRF-Token": token} if token else {}

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.http.get(url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        # Extra headers (an `Idempotency-Key`, say) join the CSRF header instead of replacing it.
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        return await self.http.post(url, headers=headers, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.http.patch(url, headers=self._headers(), **kwargs)

    async def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.http.put(url, headers=self._headers(), **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        # `request`, not `delete`: deleting an organization or a project carries a JSON body.
        return await self.http.request("DELETE", url, headers=self._headers(), **kwargs)


@dataclass
class Workspace:
    owner: Browser
    org_id: str
    project_id: str
    api_key: str


async def create_workspace(owner: Browser, *, org_name: str = "Acme") -> Workspace:
    org = await owner.post("/api/v1/orgs", json={"name": org_name})
    assert org.status_code == 201, org.text
    org_id = org.json()["id"]
    project = await owner.post(f"/api/v1/orgs/{org_id}/projects", json={"name": "Chatbot"})
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    key = await owner.post(f"/api/v1/projects/{project_id}/keys", json={"name": "ci"})
    assert key.status_code == 201, key.text
    return Workspace(
        owner=owner, org_id=org_id, project_id=project_id, api_key=key.json()["secret"]
    )


def new_trace_id() -> str:
    return secrets.token_hex(16)


def new_span_id() -> str:
    return secrets.token_hex(8)


def span(
    *,
    trace_id: str,
    span_id: str | None = None,
    start: datetime | None = None,
    duration_ms: float = 100.0,
    **overrides: Any,
) -> dict[str, Any]:
    started = start or datetime.now(UTC) - timedelta(minutes=5)
    body: dict[str, Any] = {
        "trace_id": trace_id,
        "span_id": span_id or new_span_id(),
        "name": "chat gpt-4o-mini",
        "kind": "llm",
        "status": "ok",
        "start_time": started.isoformat(),
        "end_time": (started + timedelta(milliseconds=duration_ms)).isoformat(),
        "provider": "openai",
        "model": "gpt-4o-mini",
        "usage": {"input_tokens": 1000, "output_tokens": 500, "cached_tokens": 0},
        "input": {"messages": [{"role": "user", "content": "hi"}]},
        "output": {"role": "assistant", "content": "hello"},
        "attributes": {"temperature": 0.2},
        "trace": {"name": "answer", "environment": "prod", "release": "v1", "tags": ["beta"]},
    }
    body.update(overrides)
    return body


async def count_project_rows(
    session_factory: async_sessionmaker[AsyncSession], project_id: str
) -> dict[str, int]:
    """Traces, spans and API keys kept for a project, counted past row-level security.

    Like the worker, the count sets the bypass flag: the app role sees no telemetry without a
    bound project, and a deleted project can no longer be bound.
    """
    async with session_factory() as db:
        await db.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
        counts: dict[str, int] = {}
        for table in ("traces", "spans", "api_keys"):
            counts[table] = (
                await db.execute(
                    text(f"SELECT count(*) FROM {table} WHERE project_id = :id"),
                    {"id": project_id},
                )
            ).scalar_one()
        return counts


def bearer(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


async def join_with_role(owner: Browser, member: Browser, org_id: str, role: str) -> None:
    invite = await owner.post(f"/api/v1/orgs/{org_id}/invites", json={"role": role})
    assert invite.status_code == 201, invite.text
    token = invite.json()["url"].rsplit("/", 1)[1]
    accepted = await member.post("/api/v1/invites/accept", json={"token": token})
    assert accepted.status_code == 200, accepted.text


async def create_project(owner: Browser, org_id: str, name: str) -> str:
    response = await owner.post(f"/api/v1/orgs/{org_id}/projects", json={"name": name})
    assert response.status_code == 201, response.text
    project_id: str = response.json()["id"]
    return project_id


async def create_api_key(owner: Browser, project_id: str, **body: Any) -> dict[str, Any]:
    """Create a key through the API and return the 201 body (it includes the one-time `secret`)."""
    response = await owner.post(f"/api/v1/projects/{project_id}/keys", json={"name": "k", **body})
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def expire_api_key(session_factory: async_sessionmaker[AsyncSession], key_id: str) -> None:
    """Backdate a key's expiry: the API refuses to create a key that is already expired."""
    async with session_factory() as db:
        await db.execute(
            text("UPDATE api_keys SET expires_at = now() - interval '1 minute' WHERE id = :id"),
            {"id": key_id},
        )
        await db.commit()


async def create_token(owner: Browser, **body: Any) -> dict[str, Any]:
    """Create a token through the API and return the 201 body (it includes the one-time `token`)."""
    response = await owner.post(
        "/api/v1/auth/tokens", json={"name": "cli", "scope": "write", **body}
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


async def expire_token(session_factory: async_sessionmaker[AsyncSession], token_id: str) -> None:
    """Backdate a token's expiry: the API refuses to create a token that is already expired."""
    async with session_factory() as db:
        await db.execute(
            text(
                "UPDATE personal_access_tokens SET expires_at = now() - interval '1 minute' "
                "WHERE id = :id"
            ),
            {"id": token_id},
        )
        await db.commit()

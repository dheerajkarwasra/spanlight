"""Helpers for the billing source route tests: an app whose provider calls a script answers.

- `ProviderApi`: the scripted provider API of `fetch_support`, which can also fail to connect
  (`connect_error = True`) and run `on_request` before it answers. Every request it sees is kept
  in `requests`.
- `running_app(settings, api, clock=None)`: the app built over `api` (the check's provider call
  goes there) and on `clock` when given, started; `signed_up(app, email)` a signed-in browser of it.
- `source_body(...)` and `create_source(...)`: a create body and a created source.
- `secret_windows(key)`: every 8-character slice of the key's random part, for leak checks.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from app.api.deps import Clock
from app.config import Settings
from app.main import create_app
from tests.gateway_support import public_resolver
from tests.helpers import Browser
from tests.reconciliation.fetch_support import ANTHROPIC_KEY, OPENAI_KEY, Reply, ScriptedApi

TEST_ORIGIN = "http://testserver"
# A second admin key, for rotation.
ANTHROPIC_KEY_2 = "sk-ant-admin01-Hw4Tz9Qp2Lk7Vn3Bx8Rc5Md1Fs6Gj0Ya"
KEY_FOR = {"anthropic_admin_api": ANTHROPIC_KEY, "openai_costs_api": OPENAI_KEY}
NOT_ADMIN_KEY = "does not look like an Admin API key"
NEEDS_KEYS = (
    "Billing sources that fetch from a provider are encrypted with a server key that isn't "
    "set. Ask your administrator to set CREDENTIALS_KEYS, or add a CSV source."
)


@dataclass
class ProviderApi(ScriptedApi):
    connect_error: bool = False
    # Awaited before each answer: a test looks at the database while the provider is called.
    on_request: Callable[[], Awaitable[None]] | None = None

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.on_request is not None:
            await self.on_request()
        if self.connect_error:
            self.requests.append(request)
            raise httpx.ConnectError("connection refused", request=request)
        return await super().__call__(request)


def answering(status: int = 200) -> ProviderApi:
    """A provider that answers every request with an empty page (or `status`)."""
    body = {"data": [], "has_more": False, "next_page": None}
    return ProviderApi(default=Reply(status=status, json=body))


@asynccontextmanager
async def running_app(
    settings: Settings, api: ProviderApi, clock: Clock | None = None
) -> AsyncIterator[Any]:
    application = create_app(
        settings,
        gateway_transport=httpx.MockTransport(api),
        gateway_resolver=public_resolver,
        clock=clock,
    )
    async with application.router.lifespan_context(application):
        yield application


async def signed_up(app: Any, email: str) -> Browser:
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=TEST_ORIGIN,
        headers={"Origin": TEST_ORIGIN},
    )
    response = await http.post(
        "/api/v1/auth/signup",
        json={"email": email, "password": "correct horse battery", "name": "Owner"},
    )
    assert response.status_code == 201, response.text
    return Browser(http=http, user=response.json())


def source_body(
    kind: str = "anthropic_admin_api",
    *,
    name: str = "Prod account",
    project_ids: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {"kind": kind, "name": name, "project_ids": project_ids or []}
    if kind in KEY_FOR and "admin_key" not in extra:
        body["admin_key"] = KEY_FOR[kind]
    return {**body, **extra}


def sources_url(org_id: str) -> str:
    return f"/api/v1/orgs/{org_id}/billing-sources"


async def create_source(owner: Browser, org_id: str, **body: Any) -> dict[str, Any]:
    response = await owner.post(sources_url(org_id), json=source_body(**body))
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def secret_windows(key: str) -> set[str]:
    """Every 8-character slice of the key after its well-known prefix."""
    secret = key.split("-", 3)[-1]
    return {secret[start : start + 8] for start in range(len(secret) - 7)}

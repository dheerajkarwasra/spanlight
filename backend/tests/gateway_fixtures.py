"""Gateway fixtures for every test directory; `tests/conftest.py` registers this module.

What each fixture returns is listed in `tests/gateway_support.py`, which holds the helpers.
"""

import base64
import dataclasses
import random
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import dataclass
from typing import Any, Literal

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.config import Settings
from app.core.security import GATEWAY_KEY_PREFIX, parse_key
from app.db.models import Project, ProviderCredential
from app.db.rls import bind_project
from app.gateway import recorder
from app.gateway.context import GatewayContext, SpanSink, context_for_key
from app.gateway.in_process import context_for_project, single_credential_context
from app.gateway.key_context import find_key, load_key_context
from app.gateway.runtime import GatewayRuntime
from app.main import create_app
from tests.gateway_support import FakeProvider, ManualClock, drain_recorder, public_resolver
from tests.helpers import Browser

TEST_ORIGIN = "http://testserver"
CREDENTIALS_KEYS = "gateway-test:" + base64.b64encode(bytes(range(32))).decode()
# Fake provider keys: the fake provider sees them, nothing else does.
OPENAI_API_KEY = "sk-test-openai-gateway-fixture"
ANTHROPIC_API_KEY = "sk-ant-test-gateway-fixture"
DEFAULT_SEED = 1234

GatewayContextFactory = Callable[..., Awaitable[GatewayContext]]


@dataclass
class GatewayWorkspace:
    """An organization ready for gateway calls, made through the API."""

    owner: Browser  # the org owner, signed in to `/api/v1`
    gw: httpx.AsyncClient  # sends `Authorization: Bearer <gateway_key>`
    org_id: uuid.UUID
    project_id: uuid.UUID
    openai_credential_id: uuid.UUID
    anthropic_credential_id: uuid.UUID
    route_id: uuid.UUID  # the project's default route: OpenAI target, then Anthropic
    gateway_key: str  # the raw `spl_gw_…` secret
    gateway_key_id: uuid.UUID


@pytest.fixture
def gateway_settings(settings: Settings) -> Settings:
    """The test settings with `credentials_keys` set, so credentials can be sealed."""
    return settings.model_copy(update={"credentials_keys": SecretStr(CREDENTIALS_KEYS)})


@pytest.fixture
def fake_provider() -> Iterator[FakeProvider]:
    provider = FakeProvider()
    yield provider
    unscripted = [f"{sent.method} {sent.url}" for sent in provider.unscripted]
    assert not unscripted, f"the fake provider got requests nobody scripted: {unscripted}"


@pytest.fixture
async def gateway_app(
    gateway_settings: Settings, fake_provider: FakeProvider
) -> AsyncIterator[Any]:
    application = create_app(
        gateway_settings, gateway_transport=fake_provider, gateway_resolver=public_resolver
    )
    async with application.router.lifespan_context(application):
        yield application


def _client(app: Any, headers: dict[str, str]) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url=TEST_ORIGIN, headers=headers)


@pytest.fixture
async def gateway_client(gateway_app: Any) -> AsyncIterator[httpx.AsyncClient]:
    async with _client(gateway_app, {"Origin": TEST_ORIGIN}) as http:
        yield http


async def _created(owner: Browser, url: str, body: dict[str, Any]) -> dict[str, Any]:
    response = await owner.post(url, json=body)
    assert response.status_code == 201, response.text
    result: dict[str, Any] = response.json()
    return result


async def _sign_up(app: Any) -> Browser:
    http = _client(app, {"Origin": TEST_ORIGIN})
    response = await http.post(
        "/api/v1/auth/signup",
        json={"email": "owner@example.com", "password": "correct horse battery", "name": "Owner"},
    )
    assert response.status_code == 201, response.text
    return Browser(http=http, user=response.json())


async def _build_workspace(app: Any, owner: Browser) -> GatewayWorkspace:
    org = await _created(owner, "/api/v1/orgs", {"name": "Gateway Org"})
    project = await _created(owner, f"/api/v1/orgs/{org['id']}/projects", {"name": "Gateway"})
    credentials = f"/api/v1/orgs/{org['id']}/credentials"
    openai = await _created(
        owner, credentials, {"name": "openai", "provider": "openai", "api_key": OPENAI_API_KEY}
    )
    anthropic = await _created(
        owner,
        credentials,
        {"name": "anthropic", "provider": "anthropic", "api_key": ANTHROPIC_API_KEY},
    )
    gateway = f"/api/v1/projects/{project['id']}/gateway"
    targets = [{"credential_id": openai["id"]}, {"credential_id": anthropic["id"]}]
    config = {"targets": targets, "retry": {}, "fallback": {}}
    route = await _created(owner, f"{gateway}/routes", {"name": "main", "config": config})
    key = await _created(owner, f"{gateway}/keys", {"name": "tests", "environment": "test"})
    return GatewayWorkspace(
        owner=owner,
        gw=_client(app, {"Authorization": f"Bearer {key['secret']}"}),
        org_id=uuid.UUID(org["id"]),
        project_id=uuid.UUID(project["id"]),
        openai_credential_id=uuid.UUID(openai["id"]),
        anthropic_credential_id=uuid.UUID(anthropic["id"]),
        route_id=uuid.UUID(route["id"]),
        gateway_key=key["secret"],
        gateway_key_id=uuid.UUID(key["id"]),
    )


@pytest.fixture
async def gateway_workspace(gateway_app: Any) -> AsyncIterator[GatewayWorkspace]:
    owner = await _sign_up(gateway_app)
    try:
        workspace = await _build_workspace(gateway_app, owner)
        try:
            yield workspace
        finally:
            await workspace.gw.aclose()
    finally:
        await owner.http.aclose()


async def _single_credential(
    runtime: GatewayRuntime,
    workspace: GatewayWorkspace,
    which: Literal["openai", "anthropic"],
    environment: str,
    span_sink: SpanSink | None,
) -> GatewayContext:
    """`single_credential_context` on one of the workspace's credentials (the judges' shape)."""
    credential_id = (
        workspace.openai_credential_id if which == "openai" else workspace.anthropic_credential_id
    )
    async with runtime.sessions() as db, db.begin():
        await bind_project(db, workspace.project_id)
        project = await db.scalar(select(Project).where(Project.id == workspace.project_id))
        credential = await db.get(ProviderCredential, credential_id)
    assert project is not None and credential is not None
    sink: SpanSink = span_sink if span_sink is not None else recorder.DirectSpans()
    context = single_credential_context(
        runtime, project, credential, environment=environment, route_name="tests", span_sink=sink
    )
    # No sink given: spans go to the background recorder, like a call through a stored route.
    return context if span_sink is not None else dataclasses.replace(context, span_sink=None)


async def _keyed(runtime: GatewayRuntime, workspace: GatewayWorkspace) -> GatewayContext:
    """The context the `/gw/v1` router builds for the workspace's key (its limits not checked)."""
    parsed = parse_key(workspace.gateway_key, GATEWAY_KEY_PREFIX)
    assert parsed is not None
    async with runtime.sessions() as db, db.begin():
        key = await find_key(db, parsed.prefix)
        assert key is not None
        key_context = await load_key_context(db, key)
    assert key_context is not None
    return context_for_key(key_context, runtime)


@pytest.fixture
async def gateway_context(
    gateway_app: Any, gateway_workspace: GatewayWorkspace
) -> AsyncIterator[GatewayContextFactory]:
    """`await gateway_context(...)` → a `GatewayContext` on the workspace's project.

    Keywords: `credential` ("openai" or "anthropic": a one-attempt in-memory route on that
    credential, as judges and explanations use; None: the stored default route), `keyed` (True:
    the context of a call made with the workspace's gateway key, through the stored route, whose
    spans also count in the key's minute; `environment` is then the key's), `environment`,
    `seed` (the `random.Random` seed), `clock` (a `ManualClock` for wall and monotonic time;
    real time when None) and `span_sink` (None: the background recorder).
    """
    runtime: GatewayRuntime = gateway_app.state.gateway_runtime

    async def build(
        *,
        credential: Literal["openai", "anthropic"] | None = None,
        keyed: bool = False,
        environment: str = "test",
        seed: int = DEFAULT_SEED,
        clock: ManualClock | None = None,
        span_sink: SpanSink | None = None,
    ) -> GatewayContext:
        if credential is not None:
            context = await _single_credential(
                runtime, gateway_workspace, credential, environment, span_sink
            )
        elif keyed:
            context = await _keyed(runtime, gateway_workspace)
            context = dataclasses.replace(context, span_sink=span_sink)
        else:
            async with runtime.sessions() as db, db.begin():
                context = await context_for_project(
                    db,
                    gateway_workspace.project_id,
                    route_id=None,
                    environment=environment,
                    services=runtime,
                )
            context = dataclasses.replace(context, span_sink=span_sink)
        context = dataclasses.replace(context, rng=random.Random(seed))  # noqa: S311
        if clock is not None:
            context = dataclasses.replace(context, clock=clock.wall, monotonic=clock.monotonic)
        return context

    try:
        yield build
    finally:
        await drain_recorder()

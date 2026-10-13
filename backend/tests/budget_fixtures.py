"""Budget fixtures for every test directory; `tests/conftest.py` registers this module.

- `app_clock`: the app's wall clock, pinned to yesterday 12:00 UTC (`tests/budgets/clock.py`);
  it moves only when a test moves it.
- `budget_world`: a `BudgetWorld` (`tests/budgets/support.py`) on the gateway workspace.

A directory whose tests need the gateway on `app_clock` overrides `gateway_app` in its
`conftest.py` with `app_on_clock`; the shared `gateway_app` runs on real time.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from tests.budgets.clock import pinned_start
from tests.budgets.support import BudgetWorld, Sessions
from tests.gateway_fixtures import TEST_ORIGIN, GatewayWorkspace
from tests.gateway_support import FakeProvider, ManualClock, public_resolver
from tests.helpers import bearer, create_api_key

# A PagerDuty routing key is 32 letters and digits; nothing ever sends to it.
ROUTING_KEY = "b2" * 16


@asynccontextmanager
async def app_on_clock(
    settings: Settings, fake_provider: FakeProvider, clock: ManualClock
) -> AsyncIterator[Any]:
    """The shared `gateway_app`, started on `clock`."""
    application = create_app(
        settings,
        gateway_transport=fake_provider,
        gateway_resolver=public_resolver,
        clock=clock.wall,
    )
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
def app_clock() -> ManualClock:
    return ManualClock(start=pinned_start())


@pytest.fixture
async def budget_world(
    gateway_app: Any,
    gateway_workspace: GatewayWorkspace,
    gateway_settings: Settings,
    fake_provider: FakeProvider,
    session_factory: Sessions,
    app_clock: ManualClock,
) -> AsyncIterator[BudgetWorld]:
    owner = gateway_workspace.owner
    key = await create_api_key(owner, str(gateway_workspace.project_id))
    channel = await owner.post(
        f"/api/v1/orgs/{gateway_workspace.org_id}/alert-channels",
        json={"kind": "pagerduty", "name": "on-call", "secret": ROUTING_KEY},
    )
    assert channel.status_code == 201, channel.text
    transport = httpx.ASGITransport(app=gateway_app)
    async with httpx.AsyncClient(
        transport=transport, base_url=TEST_ORIGIN, headers=bearer(key["secret"])
    ) as ingest:
        yield BudgetWorld(
            workspace=gateway_workspace,
            ingest=ingest,
            channel_id=uuid.UUID(channel.json()["id"]),
            sessions=session_factory,
            settings=gateway_settings,
            fake=fake_provider,
            clock=app_clock,
        )

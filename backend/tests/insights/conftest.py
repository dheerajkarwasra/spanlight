"""The explain tests run the gateway on the pinned `app_clock` (`tests/budget_fixtures.py`), so
the month an explanation is counted in never changes mid-test."""

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.config import Settings
from tests.budget_fixtures import app_on_clock
from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import FakeProvider, ManualClock
from tests.insights.explain_support import ExplainWorld, Sessions, create_project


@pytest.fixture
async def gateway_app(
    gateway_settings: Settings, fake_provider: FakeProvider, app_clock: ManualClock
) -> AsyncIterator[Any]:
    """The shared `gateway_app`, started on `app_clock`."""
    async with app_on_clock(gateway_settings, fake_provider, app_clock) as application:
        yield application


@pytest.fixture
async def explain_world(
    gateway_workspace: GatewayWorkspace,
    gateway_settings: Settings,
    fake_provider: FakeProvider,
    session_factory: Sessions,
    app_clock: ManualClock,
) -> ExplainWorld:
    world = ExplainWorld(
        workspace=gateway_workspace,
        second_project_id=gateway_workspace.project_id,
        sessions=session_factory,
        settings=gateway_settings,
        fake=fake_provider,
        clock=app_clock,
    )
    world.second_project_id = await create_project(world, world.org_id, "Second")
    return world

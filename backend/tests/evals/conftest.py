"""Fixtures for the evaluator tests: the gateway on the pinned `app_clock`, so a judge and a
project `block` budget (`budget_world`, `tests/budget_fixtures.py`) share one app and clock, and
a judge world on it."""

from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.config import Settings
from tests.budget_fixtures import app_on_clock
from tests.evals.judges import JudgeWorld
from tests.evals.runs import Sessions
from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import FakeProvider, ManualClock


@pytest.fixture
async def gateway_app(
    gateway_settings: Settings, fake_provider: FakeProvider, app_clock: ManualClock
) -> AsyncIterator[Any]:
    """The shared `gateway_app`, started on `app_clock`."""
    async with app_on_clock(gateway_settings, fake_provider, app_clock) as application:
        yield application


@pytest.fixture
def judge_world(
    gateway_app: Any,
    gateway_workspace: GatewayWorkspace,
    gateway_settings: Settings,
    fake_provider: FakeProvider,
    session_factory: Sessions,
    app_clock: ManualClock,
) -> JudgeWorld:
    return JudgeWorld(
        workspace=gateway_workspace,
        sessions=session_factory,
        settings=gateway_settings,
        services=gateway_app.state.gateway_runtime,
        fake=fake_provider,
        clock=app_clock,
    )

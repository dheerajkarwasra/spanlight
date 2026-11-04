"""Consumption defaults off; scheduled observation remains available for degraded deployments."""

from datetime import timedelta

import pytest

from app.config import Settings
from app.jobs.scheduler import REPAIR_AGGREGATES, periodic_jobs
from app.jobs.tasks import TASKS


@pytest.mark.parametrize("value", [False, "", "false"])
async def test_repair_defaults_off_and_blank_uses_default(value: bool | str) -> None:
    configured = Settings(_env_file=None, aggregate_repair_enabled=value)
    assert configured.aggregate_repair_enabled is False
    assert REPAIR_AGGREGATES in periodic_jobs(configured)
    assert REPAIR_AGGREGATES.every == timedelta(minutes=5)
    assert REPAIR_AGGREGATES.max_attempts == 1
    assert "repair_aggregates" in TASKS


async def test_repair_explicit_enable() -> None:
    assert Settings(_env_file=None).aggregate_repair_enabled is False
    assert Settings(_env_file=None, aggregate_repair_enabled=True).aggregate_repair_enabled is True

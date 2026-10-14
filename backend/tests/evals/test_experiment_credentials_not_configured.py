"""An experiment run on a worker without `CREDENTIALS_KEYS` fails at once, and says why.

The api that queued the run holds the keys (the credential was sealed there); the worker is a
separate process whose environment may not. Such a run makes no provider call, ends `failed`
with `credentials_not_configured`, and its log line tells the operator what to set.
"""

import dataclasses
import logging
from typing import Any

import pytest

from app.evals.experiment_run import execute_run
from tests.evals.experiments import create_experiment, queue_run
from tests.evals.judges import JudgeWorld
from tests.gateway_support import logged


async def test_a_run_on_a_worker_without_credentials_keys_fails_before_any_call(
    judge_world: JudgeWorld, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    world = dataclasses.replace(
        judge_world, settings=judge_world.settings.model_copy(update={"credentials_keys": None})
    )
    assert not world.deps.settings.is_crypto_configured
    experiment = await create_experiment(world.workspace, items=2, max_cost_usd="1.0")
    run_id = await queue_run(world.workspace, experiment)

    await execute_run(world.deps, run_id, world.workspace.project_id)

    stored = await world.workspace.owner.get(f"{experiment.url}/runs/{run_id}")
    assert stored.status_code == 200, stored.text
    body: dict[str, Any] = stored.json()
    assert (body["status"], body["error"]) == ("failed", "credentials_not_configured")
    assert world.fake.requests == []
    [event] = logged(caplog, "experiment_run_not_started")
    assert event["error"] == "credentials_not_configured"
    assert "CREDENTIALS_KEYS" in event["hint"]

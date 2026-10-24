"""An experiment made and run through the API against the fake provider.

`create_experiment` adds a dataset of `items` items and an experiment that sends the same
messages for each of them to the workspace's OpenAI credential (`gpt-4.1-mini`: $0.40 per
million input tokens, $1.60 per million output tokens) under a cost cap of `max_cost_usd`.
`CALL_ESTIMATE` is what each call reserves under the cap, and `exact_usage` the usage whose cost
is exactly that, so a finished call frees no slack. `queue_run` starts a run; the test then
executes it with `execute_run`, as the worker's job would. `reply` scripts one chat completion,
optionally held until a `Stall` is released.
"""

import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.evals.budget import estimate_call_cost
from app.pricing.cost import load_price_book
from app.pricing.tokens import estimate_text_tokens
from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import FakeProvider, ManualClock, Stall

MODEL = "gpt-4.1-mini"
PROMPT = "Summarize the item in one short sentence."
MAX_TOKENS = 1000


@dataclass(frozen=True)
class ExperimentRef:
    experiment_id: uuid.UUID
    url: str  # `/api/v1/projects/<id>/experiments/<id>`


def exact_usage() -> tuple[int, int]:
    """Prompt and completion tokens whose cost is exactly `call_estimate`."""
    return estimate_text_tokens(PROMPT), MAX_TOKENS


async def call_estimate(sessions: async_sessionmaker[AsyncSession], clock: ManualClock) -> Decimal:
    """What one call of the experiment reserves: the prompt and every allowed output token."""
    async with sessions() as db:
        prices = await load_price_book(db)
    estimate = estimate_call_cost(
        MODEL, PROMPT, MAX_TOKENS, prices, provider="openai", at=clock.wall()
    )
    assert estimate is not None
    return estimate


async def create_experiment(
    workspace: GatewayWorkspace,
    *,
    items: int,
    max_cost_usd: str,
    evaluator_ids: Sequence[uuid.UUID] = (),
) -> ExperimentRef:
    project = f"/api/v1/projects/{workspace.project_id}"
    dataset = await workspace.owner.post(f"{project}/datasets", json={"name": "cap-items"})
    assert dataset.status_code == 201, dataset.text
    dataset_id = dataset.json()["id"]
    added = await workspace.owner.post(
        f"{project}/datasets/{dataset_id}/items",
        json={"items": [{"input": f"item {number}"} for number in range(items)]},
    )
    assert added.status_code == 201, added.text
    config = {
        "target": {
            "kind": "messages",
            "credential_id": str(workspace.openai_credential_id),
            "model": MODEL,
            "messages": [{"role": "user", "content": PROMPT}],
            "params": {"max_tokens": MAX_TOKENS},
        },
        "max_cost_usd": max_cost_usd,
    }
    response = await workspace.owner.post(
        f"{project}/experiments",
        json={
            "name": "cap",
            "dataset_id": dataset_id,
            "config": config,
            "evaluator_ids": [str(evaluator_id) for evaluator_id in evaluator_ids],
        },
    )
    assert response.status_code == 201, response.text
    experiment_id = response.json()["id"]
    return ExperimentRef(uuid.UUID(experiment_id), f"{project}/experiments/{experiment_id}")


async def queue_run(workspace: GatewayWorkspace, experiment: ExperimentRef) -> uuid.UUID:
    response = await workspace.owner.post(f"{experiment.url}/runs", json={})
    assert response.status_code == 202, response.text
    return uuid.UUID(response.json()["id"])


def reply(fake: FakeProvider, usage: tuple[int, int], *, hold: Stall | None = None) -> None:
    """Script one chat completion with this (prompt, completion) usage."""
    prompt, completion = usage
    body: dict[str, Any] = {
        "id": f"chatcmpl-{secrets.token_hex(8)}",
        "object": "chat.completion",
        "created": 1760000000,
        "model": MODEL,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
        ],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    }
    fake.reply_json(body, before_headers=hold)

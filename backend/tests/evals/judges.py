"""An LLM judge on the gateway workspace, run through `run_judge` against the fake provider.

`create_judge` adds a `relevance` judge on the workspace's OpenAI credential through the API
(`gpt-4.1-mini`: $0.40 per million input tokens, $1.60 per million output tokens) and builds its
scorer the way the runner does; `Judge.call` is its prepared call for `EVAL_INPUT`, so
`Judge.estimate` is what each of its runs reserves. `queue_run` adds a queued `test` run at the
clock's time; `run_once` runs one through `run_judge`. `answer` scripts the fake provider with one
chat completion, by default a verdict whose usage costs exactly the estimate. `ParkingSessions`
holds a settle at its session so a test can cancel it there.
"""

import asyncio
import dataclasses
import secrets
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import Evaluator, EvaluatorRunSource, Project
from app.db.rls import bind_project
from app.evals.evaluators.judge import JudgeCall, JudgeEvaluator
from app.evals.run_queries import insert_run
from app.evals.run_settle import RunOutcome, RunRef
from app.evals.runner_build import RunnerDeps, build_evaluator
from app.evals.runner_judge import run_judge
from app.evals.schemas import EvalInput
from app.gateway.context import GatewayServices
from app.pricing.tokens import estimate_text_tokens
from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import FakeProvider, ManualClock

KIND = "llm_judge"
JUDGE_MODEL = "gpt-4.1-mini"
VERDICT = '{"score": 0.9, "reasoning": "On topic."}'
EVAL_INPUT = EvalInput(
    trace_id=secrets.token_hex(16),
    span_id=secrets.token_hex(8),
    input="What is the capital of France?",
    output="Paris.",
)


@dataclass
class JudgeWorld:
    workspace: GatewayWorkspace
    sessions: async_sessionmaker[AsyncSession]
    settings: Settings
    services: GatewayServices  # the app's gateway runtime
    fake: FakeProvider
    clock: ManualClock  # the app's wall clock, which the runner reads too

    @property
    def deps(self) -> RunnerDeps:
        return RunnerDeps(
            sessions=self.sessions,
            settings=self.settings,
            services=self.services,
            clock=self.clock.wall,
        )


@dataclass(frozen=True)
class Judge:
    evaluator_id: uuid.UUID
    scorer: JudgeEvaluator
    call: JudgeCall
    budget: Decimal

    @property
    def estimate(self) -> Decimal:
        return self.call.estimate


async def create_judge(world: JudgeWorld, *, budget: str, max_tokens: int = 256) -> Judge:
    """A relevance judge with this monthly budget and output allowance."""
    workspace = world.workspace
    body = {
        "name": "relevance",
        "config": {
            "kind": KIND,
            "judge_template": "relevance",
            "credential_id": str(workspace.openai_credential_id),
            "model": JUDGE_MODEL,
            "max_tokens": max_tokens,
        },
        "monthly_budget_usd": budget,
    }
    url = f"/api/v1/projects/{workspace.project_id}/evaluators"
    response = await workspace.owner.post(url, json=body)
    assert response.status_code == 201, response.text
    evaluator_id = uuid.UUID(response.json()["id"])
    async with AsyncExitStack() as stack, world.sessions() as db, db.begin():
        await bind_project(db, workspace.project_id)
        project = await db.get(Project, workspace.project_id)
        evaluator = await db.get(Evaluator, evaluator_id)
        assert project is not None and evaluator is not None
        scorer = await build_evaluator(db, world.deps, project, evaluator, stack)
    assert isinstance(scorer, JudgeEvaluator)
    return Judge(evaluator_id, scorer, scorer.prepare(EVAL_INPUT), Decimal(budget))


async def queue_run(world: JudgeWorld, judge: Judge) -> RunRef:
    """A queued `test` run of the judge, started at the clock's time."""
    async with world.sessions() as db, db.begin():
        await bind_project(db, world.workspace.project_id)
        run_id = await insert_run(
            db,
            judge.evaluator_id,
            trace_id=EVAL_INPUT.trace_id,
            span_id=EVAL_INPUT.span_id,
            source=EvaluatorRunSource.TEST,
            now=world.clock.wall(),
        )
    return RunRef(run_id, world.workspace.project_id, judge.evaluator_id, KIND, "test")


async def run_once(
    world: JudgeWorld, judge: Judge, ref: RunRef, *, deps: RunnerDeps | None = None
) -> RunOutcome:
    """Run the queued run through the reservation, the call and the settle."""
    return await run_judge(
        deps or world.deps, ref, judge.scorer, EVAL_INPUT, judge.budget, store_scores=False
    )


class ParkingSessions:
    """A session factory whose sessions, once `park()` was called, wait for `release()` before
    they open; `parked` is set when one starts waiting. Stands in for `RunnerDeps.sessions` so a
    test can catch a settle half way."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._gate: asyncio.Event | None = None
        self.parked = asyncio.Event()

    def park(self) -> None:
        self._gate = asyncio.Event()

    def release(self) -> None:
        assert self._gate is not None
        self._gate.set()

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[AsyncSession]:
        if self._gate is not None:
            self.parked.set()
            await self._gate.wait()
        async with self._sessions() as db:
            yield db

    def deps(self, deps: RunnerDeps) -> RunnerDeps:
        """`deps` with these sessions."""
        return dataclasses.replace(deps, sessions=cast(async_sessionmaker[AsyncSession], self))


def estimate_usage(judge: Judge) -> tuple[int, int]:
    """Prompt and completion tokens whose cost is exactly the judge's estimate."""
    messages = judge.call.request.body["messages"]
    prompt = "".join(message["content"] for message in messages)
    return estimate_text_tokens(prompt), judge.scorer.config.max_tokens


def answer(
    fake: FakeProvider,
    usage: tuple[int, int] | None,
    *,
    content: str = VERDICT,
    status: int = 200,
) -> None:
    """Script one OpenAI chat completion with this content and (prompt, completion) usage."""
    body: dict[str, Any] = {
        "id": f"chatcmpl-{secrets.token_hex(8)}",
        "object": "chat.completion",
        "created": 1760000000,
        "model": JUDGE_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }
    if usage is not None:
        prompt, completion = usage
        body["usage"] = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        }
    fake.reply_json(body, status=status)

"""Shared setup for the demo-traffic job tests (`test_demo.py`, `test_demo_guards.py`).

The job runs its two Claude calls through the real in-process gateway against the shared
`fake_provider` (`demo_provider`, `tests/jobs/conftest.py`): no network, no spend. `keyed` makes
the settings the job needs, `run_context` claims a `demo_traffic` job to run it with, and
`script_flow` scripts the provider with the classify and answer replies of one full run.
"""

import base64
import secrets
from datetime import timedelta
from decimal import Decimal
from typing import Any

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.jobs import evaluate_all
from app.budgets.schemas import BudgetSpec
from app.budgets.service import create_budget
from app.config import Settings
from app.db.rls import bind_project, bypass_rls
from app.ingest.pipeline import IngestTarget, ingest_spans
from app.jobs.context import TaskContext
from app.jobs.queue import ClaimedJob, claim_next, enqueue
from app.services.demo import ensure_demo_workspace
from tests.budgets.clock import next_pass
from tests.gateway_support import FakeProvider, ManualClock
from tests.helpers import new_trace_id, span

SessionFactory = async_sessionmaker[AsyncSession]

# The two replies of one run cost $0.000255 together (the rates are in `test_demo.py`).
RUN_COST = Decimal("0.00025500")
TIGHT_CAP = Decimal("0.0002")
# Each priced span below spends $0.002 of `gpt-4.1-mini`: 5,000 input tokens at $0.40 per million.
SPEND_TOKENS = 5000


def anthropic_reply(text: str, input_tokens: int, output_tokens: int) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-4-5-20251001",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    }


def script_flow(provider: FakeProvider) -> None:
    """The provider's replies to one run: the classification, then the answer."""
    provider.reply_json(anthropic_reply("billing", 40, 1))
    provider.reply_json(
        anthropic_reply("Refunds are prorated and issued within 5 business days.", 120, 18)
    )


def keyed(settings: Settings, **extra: Any) -> Settings:
    """Settings the demo job runs with: an Anthropic key and a keyring to seal it with."""
    return settings.model_copy(
        update={
            "anthropic_api_key": SecretStr("sk-ant-test-key-1234567890"),
            "credentials_keys": SecretStr(
                f"v1:{base64.b64encode(secrets.token_bytes(32)).decode()}"
            ),
            **extra,
        }
    )


async def run_context(
    session_factory: SessionFactory, settings: Settings, clock: ManualClock | None = None
) -> TaskContext:
    """A claimed `demo_traffic` job (one attempt, like the scheduler enqueues it), on `clock`
    (real time when None)."""
    async with session_factory() as db:
        await enqueue(db, "demo_traffic", max_attempts=1)
        await db.commit()
        job = await claim_next(db)
    assert isinstance(job, ClaimedJob)
    if clock is None:
        return TaskContext(job=job, settings=settings, session_factory=session_factory)
    return TaskContext(
        job=job, settings=settings, session_factory=session_factory, clock=clock.wall
    )


async def fire_demo_block_budget(
    session_factory: SessionFactory, settings: Settings, clock: ManualClock
) -> None:
    """A monthly $0.001 `block` budget on the demo project, firing on `clock`: $0.002 is spent.

    Made through the budgets service and fired by a real evaluation pass, so the job's gateway
    sees the state the way production does. The spend is far below the demo's own cap.
    """
    now = clock.wall()
    async with session_factory() as db:
        workspace = await ensure_demo_workspace(db)
        await bind_project(db, workspace.project.id)
        spec = BudgetSpec.model_validate(
            {
                "name": "Monthly cap",
                "scope": "project",
                "period": "monthly",
                "amount_usd": "0.001",
                "action": "block",
            }
        )
        budget = await create_budget(
            db, workspace.org.id, workspace.project.id, workspace.user.id, spec, now=now
        )
        usage = {"input_tokens": SPEND_TOKENS, "output_tokens": 0, "cached_tokens": 0}
        started = now - timedelta(seconds=5)
        outcome = await ingest_spans(
            db,
            IngestTarget(
                project_id=workspace.project.id,
                capture_payloads=True,
                org_id=workspace.org.id,
            ),
            [span(trace_id=new_trace_id(), start=started, model="gpt-4.1-mini", usage=usage)],
            now=now,
        )
        assert outcome.accepted == 1
        await db.commit()
        rule_id = budget.rule_id
    run = await evaluate_all(session_factory, settings, now=next_pass(clock))
    assert (run.errors, run.projects_failed) == (0, 0)
    async with session_factory() as db:
        await bypass_rls(db)
        state = await db.scalar(
            text("SELECT state FROM alert_states WHERE rule_id = :id"), {"id": rule_id}
        )
    assert state == "firing"

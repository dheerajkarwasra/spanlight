"""Helpers shared by the "Explain with Claude" tests.

An `ExplainWorld` is the gateway workspace (`tests/gateway_fixtures.py`: an organization with an
Anthropic credential) running on the pinned `app_clock`, plus a second project in the same
organization. Insights and earlier explanations are written straight into the tables (no
detector has to run); explanation rows are read past row-level security, like the worker reads
them.

The explain budget is the test settings' `EXPLAIN_MONTHLY_BUDGET_USD` ($1.00). `worst_case`
computes what one explanation reserves the way the service does, so a test can leave room for
exactly `n` reservations.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.rls import bind_project
from app.insights import explain_queries, queries
from app.insights.catalogue import KIND_INFO
from app.insights.explain import MAX_OUTPUT_TOKENS, build_explain_request, worst_case_input_tokens
from app.pricing.cost import load_price_book
from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import FakeProvider, ManualClock, Stall

Sessions = async_sessionmaker[AsyncSession]

CENT_FRACTION = Decimal("0.00000001")  # the scale of `insight_explanations.cost_usd`
ANSWER = "The provider rejected the prompt because it exceeded the context window."
# `answer_body`'s usage at the seed `claude-sonnet-5-5` rates.
ANSWER_COST = Decimal("0.003")
WAIT_SECONDS = 10.0  # how long a test waits for requests it raced to finish


@dataclass
class ExplainWorld:
    workspace: GatewayWorkspace
    second_project_id: uuid.UUID  # another project of the same organization
    sessions: Sessions
    settings: Settings
    fake: FakeProvider
    clock: ManualClock  # the app's wall clock

    @property
    def org_id(self) -> uuid.UUID:
        return self.workspace.org_id

    @property
    def budget(self) -> Decimal:
        return self.settings.explain_monthly_budget_usd

    def month_start(self) -> datetime:
        return self.clock.wall().replace(day=1, hour=0, minute=0, second=0, microsecond=0)


@dataclass(frozen=True)
class ArrivalStall(Stall):
    """A `Stall` that also sets `arrived` when the request reaches the fake provider."""

    arrived: asyncio.Event = field(default_factory=asyncio.Event)

    async def wait(self) -> None:
        self.arrived.set()
        await super().wait()


def answer_body(text_: str | None = ANSWER, *, input_tokens: int = 1000) -> dict[str, Any]:
    """An Anthropic Messages answer with 100 output tokens; `text_` None has no text block.

    At the seed `claude-sonnet-5-5` rates ($2 input, $10 output per million tokens) the default
    usage costs exactly $0.003.
    """
    content = [] if text_ is None else [{"type": "text", "text": text_}]
    return {
        "id": "msg_test_explain0001",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": content,
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": 100},
    }


def explain_url(project_id: uuid.UUID, insight_id: uuid.UUID) -> str:
    return f"/api/v1/projects/{project_id}/insights/{insight_id}/explain"


async def explain(
    world: ExplainWorld, project_id: uuid.UUID, insight_id: uuid.UUID
) -> httpx.Response:
    return await world.workspace.owner.post(explain_url(project_id, insight_id))


async def seed_insight(sessions: Sessions, project_id: uuid.UUID, now: datetime) -> uuid.UUID:
    """An open insight with no cited traces; every one has the same copy, so the same prompt."""
    insight_id = uuid.uuid4()
    async with sessions() as db, db.begin():
        await bind_project(db, project_id)
        await db.execute(
            text(
                "INSERT INTO insights (id, project_id, kind, severity, status, fingerprint, "
                "title, summary, failure_layer, certainty, evidence, suggested_fix, "
                "verification, first_seen_at, last_seen_at, occurrences) "
                "VALUES (:id, :project_id, 'test', 'warning', 'open', :fingerprint, "
                "'Requests exceed the context window', 'Calls fail with a 400.', 'client', "
                "'measured', '{}', 'Trim the prompt.', 'Re-run the failing call.', :now, :now, 1)"
            ),
            {"id": insight_id, "project_id": project_id, "fingerprint": insight_id.hex, "now": now},
        )
    return insight_id


async def seed_spend(
    sessions: Sessions,
    project_id: uuid.UUID,
    insight_id: uuid.UUID,
    cost: Decimal,
    at: datetime,
    *,
    completed: bool = True,
) -> uuid.UUID:
    """An earlier explanation of `insight_id` that cost `cost`, created at `at`; with
    `completed=False`, a reservation still waiting for its outcome."""
    row_id = uuid.uuid4()
    async with sessions() as db, db.begin():
        await bind_project(db, project_id)
        await db.execute(
            text(
                "INSERT INTO insight_explanations (id, project_id, insight_id, model, text, "
                "cost_usd, created_at, completed_at) "
                "VALUES (:id, :project_id, :insight_id, 'claude-sonnet-5-5', :text, :cost, "
                ":at, :completed_at)"
            ),
            {
                "id": row_id,
                "project_id": project_id,
                "insight_id": insight_id,
                "text": "earlier" if completed else None,
                "cost": cost,
                "at": at,
                "completed_at": at if completed else None,
            },
        )
    return row_id


async def worst_case(world: ExplainWorld, project_id: uuid.UUID, insight_id: uuid.UUID) -> Decimal:
    """What one explanation of the insight reserves (`explain_service._prepare`'s worst case)."""
    async with world.sessions() as db, db.begin():
        await bind_project(db, project_id)
        insight = await queries.get_insight(db, project_id, insight_id)
        prices = await load_price_book(db, world.org_id)
    assert insight is not None
    model = world.settings.explain_model
    request = build_explain_request(insight, KIND_INFO.get(insight.kind), [], model=model)
    priced = prices.cost(
        provider="anthropic",
        model=model,
        at=world.clock.wall(),
        input_tokens=worst_case_input_tokens(request),
        output_tokens=MAX_OUTPUT_TOKENS,
        cached_tokens=None,
    )
    assert priced is not None and priced.cost_usd > 0
    return priced.cost_usd


def spend_leaving_room(budget: Decimal, room: Decimal) -> Decimal:
    """Earlier spend that leaves at least `room` (and less than a cent fraction more) unspent."""
    return (budget - room).quantize(CENT_FRACTION, rounding=ROUND_DOWN)


async def explanation_rows(sessions: Sessions, org_id: uuid.UUID) -> list[dict[str, Any]]:
    """Every explanation row of the organization, oldest first, read past row-level security."""
    async with sessions() as db, db.begin():
        await db.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
        rows = await db.execute(
            text(
                "SELECT e.* FROM insight_explanations AS e "
                "JOIN projects AS p ON p.id = e.project_id "
                "WHERE p.org_id = :org_id ORDER BY e.created_at, e.id"
            ),
            {"org_id": org_id},
        )
        return [dict(row) for row in rows.mappings()]


async def org_spend(sessions: Sessions, org_id: uuid.UUID, since: datetime) -> Decimal:
    """The organization's explanation cost since `since`, summed independently of the app."""
    rows = await explanation_rows(sessions, org_id)
    return sum((row["cost_usd"] for row in rows if row["created_at"] >= since), Decimal(0))


async def create_project(world: ExplainWorld, org_id: uuid.UUID, name: str) -> uuid.UUID:
    response = await world.workspace.owner.post(
        f"/api/v1/orgs/{org_id}/projects", json={"name": name}
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def create_org(world: ExplainWorld, name: str) -> uuid.UUID:
    response = await world.workspace.owner.post("/api/v1/orgs", json={"name": name})
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


async def other_org_with_spend(world: ExplainWorld, cost: Decimal) -> uuid.UUID:
    """A second organization of the same owner, with one explanation that cost `cost`."""
    org_id = await create_org(world, "Other Org")
    project_id = await create_project(world, org_id, "Other")
    now = world.clock.wall()
    insight_id = await seed_insight(world.sessions, project_id, now)
    await seed_spend(world.sessions, project_id, insight_id, cost, now)
    return org_id


async def wait_for_finished(tasks: list["asyncio.Task[httpx.Response]"], count: int) -> None:
    """Return once `count` of `tasks` have finished; fail the test after `WAIT_SECONDS`."""
    async with asyncio.timeout(WAIT_SECONDS):
        while sum(task.done() for task in tasks) < count:
            pending = [task for task in tasks if not task.done()]
            await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)


def probe_reservations(monkeypatch: Any) -> list[tuple[str | None, str | None]]:
    """Record `(app.bypass_rls, app.project_id)` as each reservation is inserted, read on the
    request's own session just before the insert."""
    original = explain_queries.insert_reservation
    seen: list[tuple[str | None, str | None]] = []

    async def probed(db: AsyncSession, **kwargs: Any) -> uuid.UUID:
        row = (await db.execute(_SETTINGS)).one()
        seen.append((row.bypass, row.project_id))
        return await original(db, **kwargs)

    monkeypatch.setattr(explain_queries, "insert_reservation", probed)
    return seen


_SETTINGS = text(
    "SELECT current_setting('app.bypass_rls', true) AS bypass, "
    "current_setting('app.project_id', true) AS project_id"
)


def bypass_was_off(value: str | None) -> bool:
    """Whether a recorded `app.bypass_rls` leaves row-level security enforced (unset or empty)."""
    return value in (None, "")

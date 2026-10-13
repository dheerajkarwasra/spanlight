"""The explain budget: a call that does not fit is refused before it is sent, and concurrent
explains of one organization cannot together reserve more than the budget.

The budget check, the advisory lock that serializes it per organization and the reservation
insert run in one transaction (`explain_service._reserve`). These tests hold the provider's
answer on an event, so no reservation settles (and frees budget) while the requests race, and
run the requests through a `RaceGate`, so a broken lock fails them every time, not by chance.
"""

import asyncio
import uuid
from decimal import Decimal

import pytest

from tests.gateway_support import Stall
from tests.insights.explain_support import (
    ExplainWorld,
    answer_body,
    bypass_was_off,
    explain,
    explanation_rows,
    org_spend,
    other_org_with_spend,
    probe_reservations,
    seed_insight,
    seed_spend,
    spend_leaving_room,
    wait_for_finished,
    worst_case,
)
from tests.insights.race_gate import RaceGate


def _refused(status: int, body: dict[str, object]) -> bool:
    return status == 402 and body.get("code") == "EXPLAIN_BUDGET_EXCEEDED"


async def test_explain_refuses_when_budget_exhausted(explain_world: ExplainWorld) -> None:
    world = explain_world
    project_id = world.workspace.project_id
    insight_id = await seed_insight(world.sessions, project_id, world.clock.wall())
    await seed_spend(world.sessions, project_id, insight_id, world.budget, world.clock.wall())

    response = await explain(world, project_id, insight_id)

    assert _refused(response.status_code, response.json()), response.text
    assert world.fake.requests == []
    rows = await explanation_rows(world.sessions, world.org_id)
    assert [row["text"] for row in rows] == ["earlier"]  # no reservation was left behind


async def test_concurrent_explains_respect_budget(
    explain_world: ExplainWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = explain_world
    project_id = world.workspace.project_id
    insight_id = await seed_insight(world.sessions, project_id, world.clock.wall())
    worst = await worst_case(world, project_id, insight_id)
    # Room for two worst cases and half of a third.
    spent = spend_leaving_room(world.budget, worst * Decimal("2.5"))
    await seed_spend(world.sessions, project_id, insight_id, spent, world.clock.wall())
    release = asyncio.Event()
    world.fake.reply_json(answer_body(), before_headers=Stall(release))
    world.fake.reply_json(answer_body(), before_headers=Stall(release))
    gate = RaceGate(world.sessions, requests=3)
    gate.install(monkeypatch)

    tasks = gate.tasks = [
        asyncio.create_task(explain(world, project_id, insight_id)) for _ in range(3)
    ]
    try:
        await wait_for_finished(tasks, count=1)
        [refused] = [task.result() for task in tasks if task.done()]
        assert _refused(refused.status_code, refused.json()), refused.text
        reservations = [
            row
            for row in await explanation_rows(world.sessions, world.org_id)
            if row["completed_at"] is None
        ]
        assert [row["cost_usd"] for row in reservations] == [worst, worst]
    finally:
        release.set()
        answered = await asyncio.gather(*tasks)

    assert sorted(response.status_code for response in answered) == [201, 201, 402]
    assert len(world.fake.requests) == 2
    assert await org_spend(world.sessions, world.org_id, world.month_start()) <= world.budget


async def test_concurrent_explains_at_the_budget_edge_admit_exactly_one(
    explain_world: ExplainWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = explain_world
    now = world.clock.wall()
    first_project, second_project = world.workspace.project_id, world.second_project_id
    first = await seed_insight(world.sessions, first_project, now)
    second = await seed_insight(world.sessions, second_project, now)
    worst = await worst_case(world, first_project, first)
    assert await worst_case(world, second_project, second) == worst
    # Room for exactly one worst case: one and a half of them.
    spent = spend_leaving_room(world.budget, worst * Decimal("1.5"))
    await seed_spend(world.sessions, first_project, first, spent, now)
    other_org = await other_org_with_spend(world, Decimal("0.25"))
    other_before = await org_spend(world.sessions, other_org, world.month_start())
    gate = RaceGate(world.sessions, requests=5)
    gate.install(monkeypatch)
    seen = probe_reservations(monkeypatch)
    release = asyncio.Event()
    world.fake.reply_json(answer_body(), before_headers=Stall(release))
    targets = [(first_project, first), (second_project, second)] * 2 + [(first_project, first)]

    tasks = gate.tasks = [asyncio.create_task(explain(world, *target)) for target in targets]
    try:
        await wait_for_finished(tasks, count=4)
        [admitted_project] = [
            project for (project, _), task in zip(targets, tasks, strict=True) if not task.done()
        ]
        for task in tasks:
            if task.done():
                refused = task.result()
                assert _refused(refused.status_code, refused.json()), refused.text
        rows = await explanation_rows(world.sessions, world.org_id)
        reservations = [row for row in rows if row["completed_at"] is None]
        assert [(row["project_id"], row["cost_usd"]) for row in reservations] == [
            (admitted_project, worst)
        ]
    finally:
        release.set()
        answered = await asyncio.gather(*tasks)

    assert sorted(response.status_code for response in answered) == [201, 402, 402, 402, 402]
    assert len(world.fake.requests) == 1  # the refused four never reached the provider
    [(bypass, bound_project)] = seen  # the one insert ran bound to its project, bypass off
    assert bypass_was_off(bypass)
    assert uuid.UUID(str(bound_project)) == admitted_project
    assert await org_spend(world.sessions, other_org, world.month_start()) == other_before

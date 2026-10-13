"""Explain sums an organization's spend without the request ever bypassing row-level security.

The month-to-date sum crosses the organization's projects, so it runs inside the database
function `explain_month_to_date_cost` (migration 0507): `SECURITY DEFINER`, scoped to the one
organization it is given, with the bypass set for its own body only. The request's transaction
stays bound to the path's project, so the reservation insert is checked by the table's policy.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.insights import explain_queries
from tests.insights.explain_support import (
    ANSWER_COST,
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
)

FUNCTION = "explain_month_to_date_cost(uuid, timestamptz)"


async def test_explain_reservation_lands_in_the_path_project_and_bypass_is_never_set(
    explain_world: ExplainWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = explain_world
    now = world.clock.wall()
    project_id = world.second_project_id
    earlier = await seed_insight(world.sessions, world.workspace.project_id, now)
    await seed_spend(world.sessions, world.workspace.project_id, earlier, Decimal("0.10"), now)
    insight_id = await seed_insight(world.sessions, project_id, now)
    other_org = await other_org_with_spend(world, Decimal("0.25"))
    seen = probe_reservations(monkeypatch)
    world.fake.reply_json(answer_body())

    response = await explain(world, project_id, insight_id)

    assert response.status_code == 201, response.text
    [(bypass, bound_project)] = seen
    assert bypass_was_off(bypass)
    assert bound_project == str(project_id)
    rows = await explanation_rows(world.sessions, world.org_id)
    [answered] = [row for row in rows if row["insight_id"] == insight_id]
    assert answered["project_id"] == project_id
    assert answered["cost_usd"] == ANSWER_COST
    assert await org_spend(world.sessions, other_org, world.month_start()) == Decimal("0.25")


async def test_month_to_date_cost_counts_one_organization_and_leaves_rls_on(
    explain_world: ExplainWorld,
) -> None:
    world = explain_world
    now = world.clock.wall()
    for project_id, cost in (
        (world.workspace.project_id, "0.10"),
        (world.second_project_id, "0.05"),
    ):
        insight_id = await seed_insight(world.sessions, project_id, now)
        await seed_spend(world.sessions, project_id, insight_id, Decimal(cost), now)
    other_org = await other_org_with_spend(world, Decimal("0.25"))

    async with world.sessions() as db, db.begin():  # no project bound, no bypass
        own = await explain_queries.month_to_date_cost(db, world.org_id, world.month_start())
        other = await explain_queries.month_to_date_cost(db, other_org, world.month_start())
        bypass = await db.scalar(text("SELECT current_setting('app.bypass_rls', true)"))
        visible = await db.scalar(text("SELECT count(*) FROM insight_explanations"))

    assert (own, other) == (Decimal("0.15"), Decimal("0.25"))
    assert await org_spend(world.sessions, world.org_id, world.month_start()) == own
    assert bypass_was_off(bypass)  # the function's bypass ended with its body
    assert visible == 0  # an unbound query in the same transaction still sees nothing


async def test_month_to_date_cost_of_an_unknown_organization_is_zero(
    explain_world: ExplainWorld,
) -> None:
    world = explain_world
    insight_id = await seed_insight(world.sessions, world.workspace.project_id, world.clock.wall())
    await seed_spend(
        world.sessions, world.workspace.project_id, insight_id, Decimal("0.10"), world.clock.wall()
    )

    async with world.sessions() as db, db.begin():
        total = await explain_queries.month_to_date_cost(db, uuid.uuid4(), world.month_start())

    assert total == Decimal(0)


async def test_the_spend_function_is_a_narrow_security_definer(
    explain_world: ExplainWorld,
) -> None:
    async with explain_world.sessions() as db:
        row = (
            await db.execute(
                text(
                    "SELECT prosecdef, proconfig, "
                    # A NULL ACL is the default one, which lets PUBLIC execute.
                    "(proacl IS NULL OR EXISTS (SELECT 1 FROM aclexplode(proacl) "
                    "WHERE grantee = 0)) AS public_execute "
                    "FROM pg_proc WHERE oid = CAST(:function AS regprocedure)"
                ),
                {"function": FUNCTION},
            )
        ).one()

    assert row.prosecdef is True
    assert "search_path=pg_catalog, pg_temp" in row.proconfig
    assert row.public_execute is False  # only the roles granted EXECUTE may call it

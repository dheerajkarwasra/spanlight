"""1026 money/DDL contract in an isolated database; HTTP enforcement at current head."""

import uuid
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit

import psycopg
from sqlalchemy import text

from alembic import command
from app.db.migrations import alembic_config, downgrade_to
from app.db.rls import bind_project
from tests.budgets.support import BudgetWorld, chat, create_budget
from tests.conftest import _base_database_url, _libpq
from tests.database_lifecycle import owned_database
from tests.db import schema_probe as probe


def test_1026_round_trip_preserves_legacy_charge_and_marks_unknown_history(
    database_server: None,
) -> None:
    name = f"spanlight_test_{uuid.uuid4().hex}_main"
    base = _libpq(_base_database_url())
    url = urlunsplit(urlsplit(base)._replace(path=f"/{name}"))
    project, budget, rule = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with owned_database(base, name):
        command.upgrade(alembic_config(url), "1026")
        with psycopg.connect(url) as db:
            probe.seed_project(db, project, project)
            db.execute(
                "INSERT INTO alert_rules "
                "(id, project_id, name, kind, metric, comparator, threshold) "
                "VALUES (%s, %s, 'Budget', 'budget', 'spend', 'gt', 1)",
                (rule, project),
            )
            db.execute(
                "INSERT INTO budgets (id, project_id, name, scope, period, amount_usd, "
                "action, rule_id) VALUES (%s, %s, 'Budget', 'project', 'daily', 1, 'block', %s)",
                (budget, project, rule),
            )
            db.execute(
                "INSERT INTO budget_counters (budget_id, project_id, period_start, settled_usd) "
                "VALUES (%s, %s, date_trunc('day', now()), 0.3)",
                (budget, project),
            )
            db.execute(
                "INSERT INTO budget_reservations (id, project_id, budget_id, period_start, "
                "trace_id, span_id, amount_usd, status, expires_at, charged_usd) VALUES "
                "(%s, %s, %s, date_trunc('day', now()), "
                "'settled', 'span', .1, 'settled', now(), .1), "
                "(%s, %s, %s, date_trunc('day', now()), "
                "'reaped', 'span', .2, 'reaped', now(), NULL)",
                (uuid.uuid4(), project, budget, uuid.uuid4(), project, budget),
            )
        downgrade_to(url, "1025")
        assert "charged_usd" not in probe.columns(url, "budget_reservations")
        command.upgrade(alembic_config(url), "1026")
        with psycopg.connect(url) as db:
            assert db.execute(
                "SELECT settled_usd, reserved_usd, legacy_unattributed_usd FROM budget_counters"
            ).fetchone() == (Decimal("0.3"), Decimal(0), Decimal("0.1"))
            assert db.execute("SELECT trace_id, status FROM budget_reservations").fetchall() == [
                ("reaped", "reaped")
            ]
            assert db.execute(
                "SELECT budget_history_missing_through IS NOT NULL FROM projects"
            ).fetchone() == (True,)
            assert db.execute(
                "SELECT prosecdef FROM pg_proc WHERE proname = 'mark_missing_budget_history'"
            ).fetchone() == (False,)
        for table in ("budget_counters", "budget_reservations"):
            probe.assert_project_policies(url, table)


async def test_migration_history_marker_blocks_gateway_at_current_head(
    budget_world: BudgetWorld,
) -> None:
    world = budget_world
    await create_budget(world, amount=Decimal("1"))
    async with world.sessions() as db, db.begin():
        await bind_project(db, world.workspace.project_id)
        await db.execute(
            text("UPDATE projects SET budget_history_missing_through = now() WHERE id = :id"),
            {"id": world.workspace.project_id},
        )
    response = await chat(world, scripted_tokens=0)
    assert response.status_code == 402
    assert "history is missing" in response.json()["error"]["message"]
    assert world.fake.requests == []

"""Migrations 1032 and 1033 in an isolated database: the bucket tables, their policies, the
statement triggers (one plpgsql body per `TG_OP`, each naming only its own transition tables;
deltas for untracked writes, bumps for the rest) and both downgrades."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg

from alembic import command
from app.db.migrations import alembic_config, downgrade_to
from tests.budgets.test_deleted_spend_migration import _database_at, _seed_project
from tests.db import schema_probe as probe

TRACE = "e" * 32
SPAN = "0123456789abcdef"
HOUR = datetime(2026, 3, 4, 10, tzinfo=UTC)


def _seed_budget(db: psycopg.Connection, project_id: uuid.UUID) -> uuid.UUID:
    rule_id, budget_id = uuid.uuid4(), uuid.uuid4()
    db.execute(
        "INSERT INTO alert_rules (id, project_id, name, kind, metric, comparator, "
        "window_minutes, threshold) VALUES (%s, %s, 'rule', 'threshold', 'error_rate', 'gt', 5, 1)",
        (rule_id, project_id),
    )
    db.execute(
        "INSERT INTO budgets (id, project_id, name, scope, period, amount_usd, action, rule_id) "
        "VALUES (%s, %s, 'cap', 'project', 'daily', 1, 'block', %s)",
        (budget_id, project_id, rule_id),
    )
    return budget_id


def _versions(db: psycopg.Connection, project_id: uuid.UUID) -> dict[datetime, int]:
    rows = db.execute(
        "SELECT bucket_start, generation FROM budget_bucket_versions WHERE project_id = %s",
        (project_id,),
    ).fetchall()
    return {bucket_start: generation for bucket_start, generation in rows}


def _insert_span(db: psycopg.Connection, project_id: uuid.UUID, started_at: datetime) -> None:
    db.execute(
        "INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, started_at, "
        "ended_at, cost_usd) VALUES (%s, %s, %s, 'llm', 'call', 'ok', %s, %s, 0.5)",
        (project_id, TRACE, SPAN, started_at, started_at),
    )


def test_snapshot_tables_policies(database_server: None) -> None:
    with _database_at("1032") as url:
        probe.assert_project_policies(url, "budget_bucket_versions")
        probe.assert_project_policies(url, "budget_bucket_snapshots")
        assert probe.columns(url, "budget_bucket_snapshots")["committed_usd"][:4] == (
            "numeric",
            "NO",
            18,
            8,
        )
        assert "budget_reservations_budget_expires_idx" in probe.indexes(url, "budget_reservations")


def test_triggers_bump_the_hours_of_every_span_and_reservation_change(
    database_server: None,
) -> None:
    with _database_at("1032") as url, psycopg.connect(url, autocommit=True) as db:
        _, project_id = _seed_project(db, 90)
        db.execute(
            "INSERT INTO traces (project_id, trace_id, started_at, ended_at) "
            "VALUES (%s, %s, %s, %s)",
            (project_id, TRACE, HOUR, HOUR),
        )
        _insert_span(db, project_id, HOUR + timedelta(minutes=5))
        assert _versions(db, project_id) == {}  # no budget yet: nothing to invalidate
        db.execute("DELETE FROM spans WHERE project_id = %s", (project_id,))
        _seed_budget(db, project_id)

        _insert_span(db, project_id, HOUR + timedelta(minutes=5))
        inserted = _versions(db, project_id)
        assert set(inserted) == {HOUR}

        db.execute(  # moved to the next hour: both hours are bumped
            "UPDATE spans SET started_at = %s, ended_at = %s WHERE project_id = %s",
            (HOUR + timedelta(hours=1, minutes=5),) * 2 + (project_id,),
        )
        moved = _versions(db, project_id)
        assert set(moved) == {HOUR, HOUR + timedelta(hours=1)}
        assert moved[HOUR] > inserted[HOUR]

        db.execute(  # a trace-field change bumps the hours of the trace's spans
            "UPDATE traces SET external_user_id = 'u1' WHERE project_id = %s", (project_id,)
        )
        renamed = _versions(db, project_id)
        assert renamed[HOUR + timedelta(hours=1)] > moved[HOUR + timedelta(hours=1)]
        db.execute("UPDATE traces SET ended_at = ended_at WHERE project_id = %s", (project_id,))
        assert _versions(db, project_id) == renamed  # no scope field changed: no bump
        moved = renamed

        db.execute("DELETE FROM spans WHERE project_id = %s", (project_id,))
        deleted = _versions(db, project_id)
        assert deleted[HOUR + timedelta(hours=1)] > moved[HOUR + timedelta(hours=1)]
        assert deleted[HOUR] == moved[HOUR]  # an old-month span writes no ledger row there

        admitted = HOUR + timedelta(hours=5, minutes=30)
        budget_id = db.execute(
            "SELECT id FROM budgets WHERE project_id = %s", (project_id,)
        ).fetchone()
        assert budget_id is not None
        db.execute(
            "INSERT INTO budget_reservations (id, project_id, budget_id, period_start, "
            "trace_id, span_id, amount_usd, expires_at) VALUES (%s, %s, %s, %s, %s, %s, 1, %s)",
            (
                uuid.uuid4(),
                project_id,
                budget_id[0],
                HOUR.replace(hour=0),
                TRACE,
                SPAN,
                admitted + timedelta(minutes=12),
            ),
        )
        assert _versions(db, project_id) == deleted  # admission bumps nothing
        db.execute(
            "UPDATE budget_reservations SET status = 'reaped' WHERE project_id = %s", (project_id,)
        )
        reaped = _versions(db, project_id)
        assert admitted.replace(minute=0) in reaped
        db.execute("DELETE FROM budget_reservations WHERE project_id = %s", (project_id,))
        assert (
            _versions(db, project_id)[admitted.replace(minute=0)]
            > reaped[admitted.replace(minute=0)]
        )


def test_downgrade_drops_triggers_and_tables(database_server: None) -> None:
    with _database_at("1032") as url:
        downgrade_to(url, "1031")
        assert probe.columns(url, "budget_bucket_versions") == {}
        assert probe.columns(url, "budget_bucket_snapshots") == {}
        assert "budget_reservations_budget_expires_idx" not in probe.indexes(
            url, "budget_reservations"
        )
        assert probe.query(
            url,
            "SELECT count(*) FROM pg_proc WHERE proname LIKE 'bump_budget_buckets_%' "
            "UNION ALL SELECT count(*) FROM pg_trigger WHERE tgname LIKE '%_budget_buckets_%'",
        ) == [(0,), (0,)]
        with psycopg.connect(url, autocommit=True) as db:
            _, project_id = _seed_project(db, 90)
            db.execute(
                "INSERT INTO traces (project_id, trace_id, started_at, ended_at) "
                "VALUES (%s, %s, now(), now())",
                (project_id, TRACE),
            )
            _insert_span(db, project_id, datetime.now(UTC))
        command.upgrade(alembic_config(url), "1032")
        probe.assert_project_policies(url, "budget_bucket_versions")


def _deltas(db: psycopg.Connection, project_id: uuid.UUID) -> dict[datetime, Decimal]:
    rows = db.execute(
        "SELECT bucket_start, sum(amount_usd) FROM budget_bucket_deltas "
        "WHERE project_id = %s GROUP BY bucket_start",
        (project_id,),
    ).fetchall()
    return {bucket_start: Decimal(amount) for bucket_start, amount in rows}


def _functions(url: str) -> list[tuple[str, str]]:
    return probe.query(
        url,
        "SELECT proname, prosrc FROM pg_proc WHERE proname LIKE 'bump_budget_buckets_%' "
        "ORDER BY proname",
    )


def test_delta_table_policies(database_server: None) -> None:
    with _database_at("1033") as url:
        probe.assert_project_policies(url, "budget_bucket_deltas")
        assert probe.columns(url, "budget_bucket_deltas")["amount_usd"][:4] == (
            "numeric",
            "NO",
            18,
            8,
        )


def test_untracked_writes_append_deltas_and_tracked_ones_bump(database_server: None) -> None:
    """Migration 1033: a span nobody tracks prices its change per budget; a span with a settled
    reservation or a ledger row bumps; a held reservation's settle writes no bump."""
    with _database_at("1033") as url, psycopg.connect(url, autocommit=True) as db:
        _, project_id = _seed_project(db, 90)
        db.execute(
            "INSERT INTO traces (project_id, trace_id, started_at, ended_at, external_user_id) "
            "VALUES (%s, %s, %s, %s, 'u1')",
            (project_id, TRACE, HOUR, HOUR),
        )
        budget_id = _seed_budget(db, project_id)
        _insert_span(db, project_id, HOUR + timedelta(minutes=5))
        assert _versions(db, project_id) == {}
        assert _deltas(db, project_id) == {HOUR: Decimal("0.5")}

        db.execute(  # moved and repriced: the old value leaves its hour, the new one arrives
            "UPDATE spans SET started_at = %s, ended_at = %s, cost_usd = 0.75 "
            "WHERE project_id = %s",
            (HOUR + timedelta(hours=1, minutes=5),) * 2 + (project_id,),
        )
        assert _versions(db, project_id) == {}
        assert _deltas(db, project_id) == {
            HOUR: Decimal(0),
            HOUR + timedelta(hours=1): Decimal("0.75"),
        }
        db.execute("UPDATE spans SET ended_at = ended_at WHERE project_id = %s", (project_id,))
        assert _deltas(db, project_id) == {
            HOUR: Decimal(0),
            HOUR + timedelta(hours=1): Decimal("0.75"),
        }  # an identical write adds no row
        db.execute("UPDATE spans SET usage_rollup = true WHERE project_id = %s", (project_id,))
        assert _deltas(db, project_id) == {
            HOUR: Decimal(0),
            HOUR + timedelta(hours=1): Decimal(0),
        }

        admitted = HOUR + timedelta(hours=5, minutes=30)
        db.execute(
            "INSERT INTO budget_reservations (id, project_id, budget_id, period_start, "
            "trace_id, span_id, amount_usd, expires_at) VALUES (%s, %s, %s, %s, %s, %s, 1, %s)",
            (
                uuid.uuid4(),
                project_id,
                budget_id,
                HOUR.replace(hour=0),
                TRACE,
                SPAN,
                admitted + timedelta(minutes=12),
            ),
        )
        db.execute(  # settled in place: the delta is the settle's job, no bump
            "UPDATE budget_reservations SET status = 'settled', charged_usd = 0.5 "
            "WHERE project_id = %s",
            (project_id,),
        )
        assert _versions(db, project_id) == {}
        db.execute(  # the span is tracked now: its write bumps its hour and the admission hour
            "UPDATE spans SET cost_usd = 0.25 WHERE project_id = %s", (project_id,)
        )
        bumped = _versions(db, project_id)
        assert set(bumped) == {HOUR + timedelta(hours=1), admitted.replace(minute=0)}
        db.execute(
            "UPDATE budget_reservations SET reclassified = true WHERE project_id = %s",
            (project_id,),
        )
        assert (
            _versions(db, project_id)[admitted.replace(minute=0)]
            > bumped[admitted.replace(minute=0)]
        )
        db.execute("DELETE FROM spans WHERE project_id = %s", (project_id,))
        assert (
            _versions(db, project_id)[HOUR + timedelta(hours=1)] > bumped[HOUR + timedelta(hours=1)]
        )


def test_downgrade_restores_1032_functions(database_server: None) -> None:
    with _database_at("1032") as original, _database_at("1033") as url:
        with psycopg.connect(url, autocommit=True) as db:
            _, project_id = _seed_project(db, 90)
            budget_id = _seed_budget(db, project_id)
            db.execute(
                "INSERT INTO budget_bucket_snapshots (project_id, budget_id, bucket_start, "
                "committed_usd, generation) VALUES (%s, %s, %s, 1, -1), (%s, %s, %s, 2, 0)",
                (project_id, budget_id, HOUR, project_id, budget_id, HOUR + timedelta(hours=1)),
            )
        downgrade_to(url, "1032")
        assert _functions(url) == _functions(original)
        assert probe.columns(url, "budget_bucket_deltas") == {}
        assert probe.query(
            url, "SELECT bucket_start, generation FROM budget_bucket_snapshots ORDER BY 1"
        ) == [(HOUR + timedelta(hours=1), 0)]
        assert probe.query(
            url,
            "SELECT count(*) FROM pg_trigger WHERE tgname = "
            "'budget_reservations_budget_buckets_update' AND tgnewtable IS NULL",
        ) == [(1,)]
        with psycopg.connect(url, autocommit=True) as db:
            db.execute(
                "INSERT INTO traces (project_id, trace_id, started_at, ended_at) "
                "VALUES (%s, %s, %s, %s)",
                (project_id, TRACE, HOUR, HOUR),
            )
            _insert_span(db, project_id, HOUR + timedelta(minutes=5))
            assert set(_versions(db, project_id)) == {HOUR}  # 1032 bumps again
        command.upgrade(alembic_config(url), "1033")
        probe.assert_project_policies(url, "budget_bucket_deltas")

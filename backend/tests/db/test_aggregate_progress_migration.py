"""Admission/rollback guards on isolated schemas; no progress rows removed for setup."""

from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy.exc import DBAPIError

from alembic import command
from app.db.migrations import alembic_config
from app.db.roles import ensure_app_role
from tests.conftest import _libpq
from tests.db.migration_support import admin_database_url, downgrade_to, upgrade_to_target


def project(db: psycopg.Connection, org: object) -> object:
    identity = uuid4()
    db.execute(
        "INSERT INTO projects(id,org_id,name,slug) VALUES (%s,%s,'P',%s)",
        (identity, org, str(identity)),
    )
    return identity


def wait_for_lock(url: str, mode: str) -> None:
    deadline = monotonic() + 3
    with psycopg.connect(url, autocommit=True) as db:
        while monotonic() < deadline:
            if db.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE "
                "relation='projects'::regclass AND mode=%s AND NOT granted)",
                (mode,),
            ).fetchone()[0]:
                return
            sleep(0.01)
    raise AssertionError(f"{mode} did not wait")


@pytest.mark.migration_revision("1029")
def test_seed_and_concurrent_creation_have_both_immutable_admissions(
    migration_database_url: str,
) -> None:
    downgrade_to(migration_database_url, "1028")
    url = _libpq(migration_database_url)
    org = uuid4()
    with psycopg.connect(url) as db:
        db.execute("INSERT INTO organizations(id,name,slug) VALUES(%s,'O',%s)", (org, str(org)))
    with psycopg.connect(url) as before, ThreadPoolExecutor(max_workers=2) as pool:
        first = project(before, org)
        migration = pool.submit(command.upgrade, alembic_config(migration_database_url), "1029")
        wait_for_lock(url, "ShareRowExclusiveLock")

        def create_after():
            with psycopg.connect(url) as db:
                return project(db, org)

        creation = pool.submit(create_after)
        wait_for_lock(url, "RowExclusiveLock")
        before.commit()
        migration.result(timeout=5)
        second = creation.result(timeout=5)
    with psycopg.connect(url) as db:
        db.execute("SET LOCAL app.bypass_rls='on'")
        assert db.execute("SELECT count(*) FROM aggregate_job_progress").fetchone() == (4,)
        assert {x[0] for x in db.execute("SELECT project_id FROM aggregate_job_progress")} == {
            first,
            second,
        }
        with pytest.raises(psycopg.errors.CheckViolation, match="immutable"):
            db.execute(
                "UPDATE aggregate_job_progress SET first_period=first_period+1 WHERE "
                "kind='attribution_daily'"
            )
        db.rollback()


@pytest.mark.migration_revision("1029")
def test_downgrade_refuses_due_cursor_and_queue_but_permits_settled_history(
    migration_database_url: str,
) -> None:
    url = _libpq(migration_database_url)
    org = uuid4()
    with psycopg.connect(url) as db:
        db.execute("INSERT INTO organizations(id,name,slug) VALUES(%s,'O',%s)", (org, str(org)))
        identity = project(db, org)
        db.execute("SET LOCAL app.bypass_rls='on'")
        db.execute(
            "INSERT INTO aggregate_dirty_hours VALUES(%s,date_trunc('hour',now()),42,now(),15)",
            (identity,),
        )
        db.execute(
            "INSERT INTO aggregate_incomplete_hours(project_id,bucket_hour,reason) "
            "VALUES(%s,date_trunc('hour',now()),'retention')",
            (identity,),
        )
        dirty = db.execute("SELECT * FROM aggregate_dirty_hours").fetchall()
        coverage = db.execute("SELECT * FROM aggregate_incomplete_hours").fetchall()
        db.execute(
            "UPDATE aggregate_job_progress SET "
            "settled_period=current_date,settled_outcomes=1 WHERE kind='attribution_daily'"
        )
    with pytest.raises(DBAPIError, match="Unfinished aggregate scheduling"):
        downgrade_to(migration_database_url, "1028")
    with psycopg.connect(url) as db:
        db.execute("SET LOCAL app.bypass_rls='on'")
        # Shared schema gate only: supported closure/recovery transition is supplied by Unit5C.
        db.execute(
            "UPDATE aggregate_job_progress SET "
            "next_closed_hour=date_trunc('hour',clock_timestamp()-interval '10 minutes'),"
            "closed_pending_since=now()-interval '1 day' WHERE kind='rollup_recent'"
        )
        db.execute("INSERT INTO jobs(kind) VALUES('rollup_hourly')")
    with pytest.raises(DBAPIError, match="Aggregate scheduling jobs remain"):
        downgrade_to(migration_database_url, "1028")
    with psycopg.connect(url) as db:
        db.execute("UPDATE jobs SET status='done'")
    downgrade_to(migration_database_url, "1028")
    with psycopg.connect(url) as db:
        db.execute("SET LOCAL app.bypass_rls='on'")
        assert db.execute("SELECT to_regclass('aggregate_job_progress')").fetchone() == (None,)
        assert db.execute("SELECT * FROM aggregate_dirty_hours").fetchall() == dirty
        assert db.execute("SELECT * FROM aggregate_incomplete_hours").fetchall() == coverage
    upgrade_to_target(migration_database_url)


@pytest.mark.migration_revision("1029")
def test_ordinary_separate_app_role_has_only_inventory_grants_and_forced_rls(
    migration_database_url: str,
) -> None:

    admin = _libpq(admin_database_url())
    role = "progress_" + uuid4().hex[:12]
    ensure_app_role(admin, role, "isolated-test")
    parts = urlsplit(_libpq(migration_database_url))
    url = urlunsplit(parts._replace(netloc=f"{role}:isolated-test@{parts.hostname}:{parts.port}"))
    org = uuid4()
    with psycopg.connect(url) as db:
        assert db.execute(
            "SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        ).fetchone() == (False, False)
        db.execute("INSERT INTO organizations(id,name,slug) VALUES(%s,'O',%s)", (org, str(org)))
        identity = project(db, org)
        assert db.execute("SELECT count(*) FROM aggregate_job_progress").fetchone() == (0,)
        db.execute("SELECT set_config('app.project_id',%s,true)", (str(identity),))
        assert db.execute("SELECT count(*) FROM aggregate_job_progress").fetchone() == (2,)
        assert db.execute(
            "SELECT "
            "has_sequence_privilege(current_user,'aggregate_job_attempt_seq','USAGE'),has_sequence_privilege(current_user,'aggregate_job_attempt_seq','UPDATE')"
        ).fetchone() == (True, False)
        assert db.execute(
            "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE "
            "oid='aggregate_job_progress'::regclass"
        ).fetchone() == (True, True)
        assert db.execute(
            "SELECT bool_and(NOT prosecdef) FROM pg_proc WHERE proname IN "
            "('admit_aggregate_job_progress','aggregate_job_progress_immutable')"
        ).fetchone() == (True,)
        db.execute("DELETE FROM projects WHERE id=%s", (identity,))
        db.execute("DELETE FROM organizations WHERE id=%s", (org,))
        assert db.execute("SELECT count(*) FROM aggregate_job_progress").fetchone() == (0,)

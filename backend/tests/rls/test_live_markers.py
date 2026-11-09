"""The cross-project marker read stays narrow and restores the caller's RLS state."""

from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from app.db.roles import ensure_app_role
from tests.conftest import _libpq, admin_database_url


@pytest.fixture
def marker_db(app_database_url: str) -> Iterator[tuple[psycopg.Connection[Any], list[UUID]]]:
    projects = [uuid4(), uuid4(), uuid4()]
    with psycopg.connect(_libpq(app_database_url)) as db:
        org = uuid4()
        db.execute("INSERT INTO organizations(id,name,slug) VALUES(%s,'O',%s)", (org, str(org)))
        for project in projects:
            db.execute(
                "INSERT INTO projects(id,org_id,name,slug) VALUES(%s,%s,'P',%s)",
                (project, org, str(project)),
            )
        db.execute("SET LOCAL app.bypass_rls = 'on'")
        db.execute(
            "INSERT INTO aggregate_dirty_hours(project_id,bucket_hour,generation) VALUES "
            "(%s,'2026-11-04 00:00Z',10), (%s,'2026-11-04 00:00Z',99)",
            (projects[0], projects[2]),
        )
        db.execute(
            "INSERT INTO aggregate_dirty_shards(project_id,bucket_hour,shard,generation) VALUES "
            "(%s,'2026-11-04 00:00Z',0,10), (%s,'2026-11-04 00:00Z',1,3)",
            (projects[0], projects[1]),
        )
        db.execute("SET LOCAL app.bypass_rls = 'off'")
        db.execute("SET LOCAL app.project_id = ''")
        yield db, projects
        db.rollback()


def test_live_markers_catalog_is_narrow_and_owned_by_migrator(app_database_url: str) -> None:
    with psycopg.connect(_libpq(app_database_url)) as db:
        row = db.execute(
            "SELECT p.prosecdef, p.proconfig, pg_get_function_result(p.oid), "
            "l.lanname, p.proowner = (SELECT oid FROM pg_roles WHERE rolname=current_user), "
            "has_function_privilege('public', p.oid, 'EXECUTE') "
            "FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang "
            "WHERE p.oid=to_regprocedure('public.live_markers(uuid[])')"
        ).fetchone()
    assert row == (
        True,
        ["search_path=pg_catalog, pg_temp"],
        "TABLE(project_id uuid, row_count bigint, generation_sum numeric)",
        "plpgsql",
        True,
        False,
    )


def test_live_markers_reads_only_requested_projects_and_restores_rls(marker_db: Any) -> None:
    db, projects = marker_db
    assert db.execute(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user"
    ).fetchone() == (False, False)
    rows = db.execute("SELECT * FROM public.live_markers(%s::uuid[])", (projects[:2],)).fetchall()
    assert set(rows) == {(projects[0], 2, Decimal(20)), (projects[1], 1, Decimal(3))}
    assert db.execute("SELECT current_setting('app.bypass_rls',true)").fetchone() == ("off",)
    assert db.execute("SELECT count(*) FROM aggregate_dirty_hours").fetchone() == (0,)
    assert db.execute("SELECT count(*) FROM aggregate_dirty_shards").fetchone() == (0,)


@pytest.mark.parametrize("bypass", ["", "off", "on"])
@pytest.mark.parametrize("too_many", [False, True])
def test_live_markers_restores_exact_bypass_on_success_and_raise(
    marker_db: Any, bypass: str, too_many: bool
) -> None:
    db, projects = marker_db
    db.execute("SELECT set_config('app.bypass_rls',%s,true)", (bypass,))
    ids = [projects[0]] * (10001 if too_many else 10000)
    if too_many:
        # Recover the statement's savepoint, not the surrounding transaction or its setting.
        with pytest.raises(psycopg.errors.RaiseException, match="10000"), db.transaction():
            db.execute("SELECT * FROM public.live_markers(%s::uuid[])", (ids,))
    else:
        assert db.execute("SELECT * FROM public.live_markers(%s::uuid[])", (ids,)).fetchall() == [
            (projects[0], 2, Decimal(20))
        ]
    assert db.execute("SELECT current_setting('app.bypass_rls',true)").fetchone() == (bypass,)
    assert db.execute("SELECT count(*) FROM aggregate_dirty_hours").fetchone() == (
        2 if bypass == "on" else 0,
    )


@pytest.mark.parametrize("ids", [[], None])
def test_live_markers_empty_or_null_ids_reveal_nothing(marker_db: Any, ids: Any) -> None:
    db, _ = marker_db
    assert db.execute("SELECT * FROM public.live_markers(%s::uuid[])", (ids,)).fetchall() == []
    assert db.execute("SELECT current_setting('app.bypass_rls',true)").fetchone() == ("off",)


@pytest.mark.parametrize("bypass", ["off", "on"])
def test_live_markers_restores_bypass_when_its_query_raises(marker_db: Any, bypass: str) -> None:
    db, projects = marker_db
    db.execute("SELECT set_config('app.bypass_rls',%s,true)", (bypass,))
    # A transactional rename forces a real body failure after the bypass was enabled.
    db.execute("ALTER TABLE aggregate_dirty_shards RENAME TO hidden_dirty_shards")
    with (
        pytest.raises(psycopg.errors.UndefinedTable, match="aggregate_dirty_shards"),
        db.transaction(),
    ):
        db.execute("SELECT * FROM public.live_markers(%s::uuid[])", (projects,))
    assert db.execute("SELECT current_setting('app.bypass_rls',true)").fetchone() == (bypass,)
    assert db.execute("SELECT count(*) FROM aggregate_dirty_hours").fetchone() == (
        2 if bypass == "on" else 0,
    )


def test_live_markers_does_not_enable_an_initially_unset_bypass(app_database_url: str) -> None:
    with psycopg.connect(_libpq(app_database_url)) as db:
        assert db.execute("SELECT current_setting('app.bypass_rls',true)").fetchone() == (None,)
        assert db.execute("SELECT * FROM public.live_markers('{}'::uuid[])").fetchall() == []
        assert db.execute("SELECT current_setting('app.bypass_rls',true)").fetchone() == ("",)
        assert db.execute("SELECT count(*) FROM aggregate_dirty_hours").fetchone() == (0,)


def test_ensure_app_role_grants_live_markers_to_a_non_owner(app_database_url: str) -> None:
    role = f"live_marker_probe_{uuid4().hex}"
    admin_url = admin_database_url()
    try:
        ensure_app_role(admin_url, role, "test-password")
        with psycopg.connect(_libpq(admin_url)) as db:
            assert db.execute(
                "SELECT has_function_privilege(%s,'public.live_markers(uuid[])','EXECUTE')",
                (role,),
            ).fetchone() == (True,)
    finally:
        with psycopg.connect(_libpq(admin_url), autocommit=True) as db:
            db.execute(psycopg.sql.SQL("DROP OWNED BY {}").format(psycopg.sql.Identifier(role)))
            db.execute(psycopg.sql.SQL("DROP ROLE {}").format(psycopg.sql.Identifier(role)))

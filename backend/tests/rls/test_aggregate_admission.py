"""Admission restores tenant bindings on fresh and reused physical app-role connections."""

from uuid import uuid4

import psycopg
import pytest

from tests.conftest import _libpq


@pytest.mark.parametrize("mode", ["absent", "empty", "session", "local", "nested"])
@pytest.mark.parametrize("failing", [False, True])
def test_project_insert_restores_binding_and_failure_leaves_no_partial_admission(
    app_database_url: str,
    mode: str,
    failing: bool,
) -> None:
    org, project = uuid4(), uuid4()
    session_value = str(uuid4())
    local_value = str(uuid4())
    with psycopg.connect(_libpq(app_database_url)) as db:
        db.execute("INSERT INTO organizations(id,name,slug) VALUES(%s,'O',%s)", (org, str(org)))
        db.commit()
        if mode == "empty":
            db.execute("SET app.project_id=''")
            db.commit()
        if mode in {"session", "local", "nested"}:
            db.execute("SELECT set_config('app.project_id',%s,false)", (session_value,))
            db.commit()
        if mode in {"local", "nested"}:
            db.execute("SELECT set_config('app.project_id',%s,true)", (local_value,))
        if mode == "nested":
            db.execute("SAVEPOINT outer_binding")
            db.execute("SELECT set_config('app.project_id',%s,true)", (str(uuid4()),))
        before = db.execute(
            "SELECT current_setting('app.project_id',true),current_setting('app.bypass_rls',true)"
        ).fetchone()
        if failing:
            db.execute(
                "ALTER TABLE aggregate_job_progress ADD CONSTRAINT proof_reject "
                "CHECK(kind<>'rollup_recent') NOT VALID"
            )
        db.execute("SAVEPOINT insertion")
        statement = "INSERT INTO projects(id,org_id,name,slug) VALUES(%s,%s,'P',%s)"
        if failing:
            with pytest.raises(psycopg.errors.CheckViolation, match="proof_reject"):
                db.execute(statement, (project, org, str(project)))
            db.execute("ROLLBACK TO SAVEPOINT insertion")
            assert db.execute(
                "SELECT count(*) FROM projects WHERE id=%s", (project,)
            ).fetchone() == (0,)
            db.execute("ALTER TABLE aggregate_job_progress DROP CONSTRAINT proof_reject")
        else:
            db.execute(statement, (project, org, str(project)))
        after = db.execute(
            "SELECT current_setting('app.project_id',true),current_setting('app.bypass_rls',true)"
        ).fetchone()
        assert after[0] == (before[0] or "") and after[1] == before[1]
        assert db.execute("SELECT count(*) FROM aggregate_job_progress").fetchone() == (0,)
        if mode == "nested":
            db.execute("ROLLBACK TO SAVEPOINT outer_binding")
            assert db.execute("SELECT current_setting('app.project_id',true)").fetchone() == (
                local_value,
            )
        db.commit()
        assert db.execute("SELECT current_setting('app.project_id',true)").fetchone() == (
            session_value if mode in {"session", "local", "nested"} else "",
        )
        assert db.execute("SELECT count(*) FROM aggregate_job_progress").fetchone() == (0,)

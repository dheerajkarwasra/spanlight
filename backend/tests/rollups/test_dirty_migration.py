"""Revision 1028 is expand-only and refuses to discard outstanding repair work."""

from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest

from alembic import command
from app.db.migrations import alembic_config, downgrade_to
from tests.conftest import _base_database_url, _libpq
from tests.database_lifecycle import owned_database


def test_dirty_downgrade_preserves_coverage_and_refuses_even_zero_mask_rows(
    database_server: None,
) -> None:
    name = f"spanlight_test_{uuid4().hex}_main"
    base = _libpq(_base_database_url())
    url = urlunsplit(urlsplit(base)._replace(path=f"/{name}"))
    project = uuid4()
    with owned_database(base, name):
        command.upgrade(alembic_config(url), "1028")
        with psycopg.connect(url) as db:
            db.execute("INSERT INTO organizations (id,name,slug) VALUES (%s,'O','o')", (project,))
            db.execute(
                "INSERT INTO projects (id,org_id,name,slug) VALUES (%s,%s,'P','p')",
                (project, project),
            )
            db.execute(
                "INSERT INTO aggregate_incomplete_hours (project_id,bucket_hour,reason) "
                "VALUES (%s,'2026-01-01Z','retention')",
                (project,),
            )
            db.execute(
                "INSERT INTO aggregate_dirty_hours "
                "(project_id,bucket_hour,generation,pending_consumers) VALUES "
                "(%s,'2026-01-01Z',nextval('aggregate_dirty_generation_seq'),0)",
                (project,),
            )
            boundary = db.execute(
                "SELECT aggregate_history_unverified_before FROM projects"
            ).fetchone()
            default = db.execute(
                "SELECT column_default FROM information_schema.columns WHERE "
                "table_name='projects' AND column_name='aggregate_history_unverified_before'"
            ).fetchone()
            assert db.execute(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE "
                "oid='aggregate_dirty_hours'::regclass"
            ).fetchone() == (True, True)
            assert db.execute(
                "SELECT cycle FROM pg_sequences WHERE sequencename='aggregate_dirty_generation_seq'"
            ).fetchone() == (False,)
        with pytest.raises(Exception, match="Aggregate dirty work remains"):
            downgrade_to(url, "1027")
        with psycopg.connect(url) as db:
            db.execute("DELETE FROM aggregate_dirty_hours")
        downgrade_to(url, "1027")
        with psycopg.connect(url) as db:
            assert db.execute(
                "SELECT "
                "to_regclass('aggregate_dirty_hours'),to_regclass('aggregate_dirty_generation_seq')"
            ).fetchone() == (None, None)
            assert (
                db.execute("SELECT aggregate_history_unverified_before FROM projects").fetchone()
                == boundary
            )
            assert (
                db.execute(
                    "SELECT column_default FROM information_schema.columns WHERE "
                    "table_name='projects' AND column_name='aggregate_history_unverified_before'"
                ).fetchone()
                == default
            )
            assert db.execute("SELECT reason FROM aggregate_incomplete_hours").fetchone() == (
                "retention",
            )

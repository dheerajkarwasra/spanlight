"""1040 is reversible only after locking and draining every shard row."""

import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest

from alembic import command
from app.db.migrations import alembic_config, downgrade_to
from tests.conftest import _base_database_url, _libpq
from tests.database_lifecycle import owned_database


def test_shard_migration_up_down_up_and_refuses_even_zero_mask_work(database_server: None) -> None:
    name = f"spanlight_test_{uuid4().hex}_main"
    base = _libpq(_base_database_url())
    url = urlunsplit(urlsplit(base)._replace(path=f"/{name}"))
    project = uuid4()
    with owned_database(base, name):
        command.upgrade(alembic_config(url), "1040")
        with psycopg.connect(url) as db:
            db.execute("INSERT INTO organizations(id,name,slug) VALUES (%s,'O','o')", (project,))
            db.execute(
                "INSERT INTO projects(id,org_id,name,slug) VALUES (%s,%s,'P','p')",
                (project, project),
            )
            db.execute(
                "INSERT INTO aggregate_dirty_hours(project_id,bucket_hour,generation) "
                "VALUES (%s,'2026-01-01Z',nextval('aggregate_dirty_generation_seq'))",
                (project,),
            )
            assert db.execute(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
                "WHERE oid='aggregate_dirty_shards'::regclass"
            ).fetchone() == (True, True)
            assert (
                db.execute(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE indexname='aggregate_dirty_shards_oldest'"
                ).fetchone()
                is not None
            )
        # Keep an uncommitted row invisible to an emptiness check until the lock waits.
        with ThreadPoolExecutor(max_workers=1) as executor, psycopg.connect(url) as writer:
            writer.execute(
                "INSERT INTO aggregate_dirty_shards "
                "(project_id,bucket_hour,shard,generation,pending_consumers) "
                "VALUES (%s,'2026-01-01Z',0,nextval('aggregate_dirty_generation_seq'),0)",
                (project,),
            )
            future = executor.submit(downgrade_to, url, "1039")
            try:
                with psycopg.connect(url, autocommit=True) as observer:
                    deadline = time.monotonic() + 4
                    while not observer.execute(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE "
                        "relation='aggregate_dirty_shards'::regclass AND "
                        "mode='AccessExclusiveLock' AND NOT granted)"
                    ).fetchone()[0]:
                        assert time.monotonic() < deadline, "downgrade did not take table lock"
                        time.sleep(0.01)
                writer.commit()
                with pytest.raises(Exception, match="Aggregate dirty shard work remains"):
                    future.result(timeout=5)
            finally:
                writer.rollback()
        with psycopg.connect(url) as db:
            assert db.execute("SELECT count(*) FROM aggregate_dirty_shards").fetchone() == (1,)
            assert db.execute("SELECT version_num FROM alembic_version").fetchone() == ("1040",)
            db.execute("DELETE FROM aggregate_dirty_shards")
        downgrade_to(url, "1039")
        with psycopg.connect(url) as db:
            assert db.execute("SELECT to_regclass('aggregate_dirty_shards')").fetchone() == (None,)
            assert db.execute("SELECT count(*) FROM aggregate_dirty_hours").fetchone() == (1,)
        command.upgrade(alembic_config(url), "1040")
        with psycopg.connect(url) as db:
            assert db.execute("SELECT count(*) FROM aggregate_dirty_shards").fetchone() == (0,)
            assert db.execute("SELECT count(*) FROM aggregate_dirty_hours").fetchone() == (1,)

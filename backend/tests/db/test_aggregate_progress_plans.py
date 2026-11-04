"""Scoped migration seed and worker discovery/source-plan measurement."""

import json
from time import monotonic

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bypass_rls
from app.jobs.aggregate_progress import _DISCOVER
from tests.conftest import _libpq
from tests.db.migration_support import downgrade_to, upgrade_to_target

pytestmark = pytest.mark.serial


@pytest.mark.migration_revision("1029")
async def test_seed_and_discovery_index_with_ten_thousand_projects(
    migration_database_url: str,
    migration_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    downgrade_to(migration_database_url, "1028")
    with psycopg.connect(_libpq(migration_database_url)) as db:
        db.execute(
            "INSERT INTO organizations(id,name,slug) "
            "VALUES('10000000-0000-0000-0000-000000000001','O','o')"
        )
        db.execute("""INSERT INTO projects(id,org_id,name,slug)
          SELECT md5(n::text)::uuid,'10000000-0000-0000-0000-000000000001','P',n::text
          FROM generate_series(1,10000)n""")
    started = monotonic()
    upgrade_to_target(migration_database_url)
    elapsed = monotonic() - started
    with psycopg.connect(_libpq(migration_database_url)) as db:
        db.execute("ANALYZE aggregate_job_progress")
    async with migration_session_factory() as db:
        await bypass_rls(db)
        assert await db.scalar(text("SELECT count(*) FROM aggregate_job_progress")) == 20000
        plan = (
            await db.execute(
                text("EXPLAIN(ANALYZE,BUFFERS,FORMAT JSON) " + str(_DISCOVER)),
                {"kind": "attribution_daily", "visited": []},
            )
        ).scalar_one()
        encoded = json.dumps(plan, default=str)
        assert "aggregate_job_progress_order" in encoded
        assert '"Node Type": "Sort"' not in encoded
        print("CALIBRATION migration_projects=10000 progress_rows=20000 seconds=", elapsed)
        print("DISCOVERY_PLAN", encoded)
        await db.rollback()
    assert elapsed < 120

    async with migration_session_factory() as holder, migration_session_factory() as db:
        await bypass_rls(holder)
        locked = (
            await holder.scalars(
                text("""SELECT project_id FROM aggregate_job_progress
            WHERE kind='attribution_daily' ORDER BY attempt_order,project_id
            LIMIT 250 FOR UPDATE""")
            )
        ).all()
        await bypass_rls(db)
        plan = (
            await db.execute(
                text("EXPLAIN(ANALYZE,BUFFERS,FORMAT JSON) " + str(_DISCOVER)),
                {"kind": "attribution_daily", "visited": locked[:100]},
            )
        ).scalar_one()
        encoded = json.dumps(plan, default=str)
        assert "aggregate_job_progress_order" in encoded
        print("LOCKED_PREFIX_PLAN", encoded)
        await db.rollback()
        await holder.rollback()
    with psycopg.connect(_libpq(migration_database_url), autocommit=True) as db:
        before = db.execute("SELECT pg_relation_size('aggregate_job_progress_order')").fetchone()[0]
        db.execute("SET app.bypass_rls='on'")
        started = monotonic()
        db.execute(
            "UPDATE aggregate_job_progress SET attempt_order=nextval('aggregate_job_attempt_seq')"
        )
        db.execute(
            "UPDATE aggregate_job_progress SET attempt_order=nextval('aggregate_job_attempt_seq')"
        )
        churn = monotonic() - started
        after = db.execute("SELECT pg_relation_size('aggregate_job_progress_order')").fetchone()[0]
        db.execute("VACUUM ANALYZE aggregate_job_progress")
        vacuum = db.execute("SELECT pg_relation_size('aggregate_job_progress_order')").fetchone()[0]
        print(
            "CALIBRATION attempt_rotations=40000 seconds=",
            churn,
            "index_bytes_before=",
            before,
            "after=",
            after,
            "after_vacuum=",
            vacuum,
        )

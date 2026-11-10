"""The load seeder works on a stack that only ran migrations, like the Compose deployment."""

import json
import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import psycopg
from sqlalchemy import text

from app.api.window import TimeWindow
from app.db.migrations import upgrade_to_head
from app.db.rls import bind_project
from app.db.session import create_engine, create_session_factory
from app.metrics.service import overview
from app.rollups.compute import floor_hour
from tests.conftest import _base_database_url, _libpq
from tests.database_lifecycle import owned_database

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "load"))
import seed  # the seeder imports its siblings from the load directory


async def test_seeder_builds_rollups_and_available_reads_without_install_activation(
    database_server: None, tmp_path: Path
) -> None:
    name = f"spanlight_test_{uuid4().hex}_main"
    base = _base_database_url()
    url = urlunsplit(urlsplit(base)._replace(path=f"/{name}"))
    with owned_database(_libpq(base), name):
        upgrade_to_head(url)  # no `activate-aggregate-coverage`: the load stack never runs it
        credentials = tmp_path / "credentials.json"
        options = seed.Options(spans=1_000, credentials_file=credentials, seed=1, max_minutes=10)
        await seed.run(options, url)

        project_id = UUID(json.loads(credentials.read_text())["project_id"])
        with psycopg.connect(_libpq(url)) as db:
            rollup_hours = db.execute(
                "SELECT count(DISTINCT bucket_start) FROM span_rollups_hourly "
                "WHERE project_id = %s",
                (project_id,),
            ).fetchone()
        assert rollup_hours is not None and rollup_hours[0] > 0
        with psycopg.connect(_libpq(url)) as db:
            assert db.execute("SELECT count(*) FROM aggregate_dirty_hours").fetchone() == (0,)

        engine = create_engine(url)
        try:
            async with create_session_factory(engine)() as session:
                await bind_project(session, project_id)
                # The hours before the project's recent-rollup admission are settled history.
                admitted = await session.scalar(
                    text(
                        "SELECT admitted_hour FROM aggregate_job_progress "
                        "WHERE project_id = :p AND kind = 'rollup_recent'"
                    ),
                    {"p": project_id},
                )
                end = floor_hour(admitted)
                kpis = await overview(
                    session, project_id, TimeWindow(start=end - timedelta(days=7), end=end), None
                )
        finally:
            await engine.dispose()
        # The seeder repaired every hour its own ingestion marked: a marked hour is read from the
        # spans, and the scenarios would measure that instead of a settled project.
        assert kpis.current.aggregate_status == "complete"
        assert kpis.current.llm_calls

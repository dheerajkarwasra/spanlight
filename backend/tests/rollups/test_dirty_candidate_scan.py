"""Candidate discovery streams oldest indexes and recovers from a failed fetch."""

import json
from datetime import UTC, datetime
from typing import Any

from prometheus_client import REGISTRY
from sqlalchemy import event, text

from app.config import Settings
from app.db.rls import bind_project, bypass_rls
from app.rollups.dirty import HOURLY, mark_dirty_hours
from app.rollups.dirty_candidates import _CANDIDATE, Candidate, Scan
from tests.attribution.support import SessionFactory
from tests.rls.factories import create_project
from tests.rollups.dirty_support import dirty_rows
from tests.rollups.test_dirty_repair import repair


async def test_candidate_streams_oldest_indexes_without_grouping_or_sorting_backlog(
    session_factory: SessionFactory,
) -> None:
    hour = datetime(2026, 10, 20, 10, tzinfo=UTC)
    async with session_factory() as db:
        project = await create_project(db)
        await bind_project(db, project)
        await db.execute(
            text("""
                INSERT INTO aggregate_dirty_hours
                    (project_id, bucket_hour, generation, first_marked_at, pending_consumers)
                SELECT :p, CAST(:h AS timestamptz) + n * interval '1 hour',
                       nextval('aggregate_dirty_generation_seq'),
                       CAST(:h AS timestamptz) + n * interval '1 second', 4
                FROM generate_series(0, 4999) n
            """),
            {"p": project, "h": hour},
        )
        await db.execute(
            text("""
                INSERT INTO aggregate_dirty_shards
                    (project_id, bucket_hour, shard, generation, first_marked_at,
                     pending_consumers)
                SELECT project_id, bucket_hour, s, nextval('aggregate_dirty_generation_seq'),
                       first_marked_at, 0
                FROM aggregate_dirty_hours CROSS JOIN generate_series(0, 1) s
            """),
        )
        await bypass_rls(db)
        await db.execute(text("SET LOCAL enable_seqscan=off"))
        plan = await db.scalar(
            text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + str(_CANDIDATE)),
            {
                "enabled": HOURLY,
                "excluded": [],
                "attempt_projects": [],
                "attempt_hours": [],
                "cursor_age": None,
                "cursor_project": None,
                "cursor_hour": None,
            },
        )
        encoded = json.dumps(plan)
        assert '"Node Type": "Merge Append"' in encoded
        assert '"Index Name": "aggregate_dirty_hours_oldest"' in encoded
        assert '"Index Name": "aggregate_dirty_shards_oldest"' in encoded
        assert '"Node Type": "Aggregate"' not in encoded
        assert '"Node Type": "Sort"' not in encoded
        nodes = [plan[0]["Plan"]]
        for node in nodes:
            nodes.extend(node.get("Plans", []))
        oldest_scans = [node for node in nodes if node.get("Index Name", "").endswith("_oldest")]
        assert len(oldest_scans) == 2
        for node in oldest_scans:
            assert node["Actual Rows"] + node.get("Rows Removed by Filter", 0) <= 3
        # Equal ages in both tables and shards still yield one pair at its oldest age.
        scan = Scan()
        assert await scan.next(db, HOURLY) == Candidate(project, hour, hour)
        assert await scan.next(db, HOURLY) is None
        scan.next_round()
        assert await scan.next(db, HOURLY) == Candidate(
            project, hour.replace(hour=11), hour.replace(second=1)
        )


async def test_candidate_database_error_is_counted_and_next_fetch_repairs_work(
    session_factory: SessionFactory,
    settings: Settings,
) -> None:
    hour = datetime(2026, 10, 20, 10, tzinfo=UTC)
    async with session_factory() as db:
        project = await create_project(db)
        await bind_project(db, project)
        await mark_dirty_hours(db, project, [hour])
        await db.commit()
    engine = session_factory.kw["bind"].sync_engine
    failed = False

    def fail_first_fetch(
        _connection: Any,
        _cursor: Any,
        statement: str,
        parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> tuple[str, Any]:
        nonlocal failed
        if not failed and "aggregate_dirty_shards" in statement and "LIMIT 1" in statement:
            failed = True
            return "SELECT 1 / 0", ()
        return statement, parameters

    before = (
        REGISTRY.get_sample_value("spanlight_aggregate_repair_outcomes_total", {"outcome": "error"})
        or 0
    )
    event.listen(engine, "before_cursor_execute", fail_first_fetch, retval=True)
    try:
        await repair(session_factory, settings)
    finally:
        event.remove(engine, "before_cursor_execute", fail_first_fetch)
    assert failed
    assert (
        REGISTRY.get_sample_value("spanlight_aggregate_repair_outcomes_total", {"outcome": "error"})
        == before + 1
    )
    async with session_factory() as db:
        await bind_project(db, project)
        assert await dirty_rows(db) == []

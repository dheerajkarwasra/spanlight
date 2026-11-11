"""The raw trace count reads its time ranges from the index, with or without an environment."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from sqlalchemy import text

from app.api.window import TimeWindow
from app.db.rls import bind_project
from app.metrics.queries import _TRACE_COUNTS, _params, read_trace_counts
from tests.attribution.support import SessionFactory
from tests.helpers import create_workspace
from tests.ingest.faults import admin_libpq_url

END = datetime(2026, 11, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)


@pytest.mark.parametrize("environment", [None, "production"])
async def test_trace_count_sizes_its_ranges_as_ranges(
    browser_factory: Any, session_factory: SessionFactory, environment: str | None
) -> None:
    """Joined to the ranges, the planner sized each at a ninth of the table and, once the
    environment kept it off the index-only scan, read every trace for a one-hour count."""
    workspace = await create_workspace(await browser_factory("trace-count-plan@example.com"))
    project = uuid.UUID(workspace.project_id)
    groups = [[TimeWindow(END - HOUR, END)], [TimeWindow(END - 2 * HOUR, END - HOUR)]]
    async with session_factory() as db:
        await bind_project(db, project)
        # 20 000 traces, one every two minutes back from END; every third one is staging.
        await db.execute(
            text("""
                INSERT INTO traces (project_id, trace_id, started_at, ended_at, environment)
                SELECT :project, lpad(to_hex(n), 32, '0'), :end - n * interval '2 minutes',
                       :end - n * interval '2 minutes',
                       CASE WHEN n % 3 = 0 THEN 'staging' ELSE 'production' END
                FROM generate_series(1, 20000) AS n
            """),
            {"project": project, "end": END},
        )
        # The plan is only meaningful with true statistics, which only committed rows give.
        await db.commit()
        async with await psycopg.AsyncConnection.connect(
            admin_libpq_url(), autocommit=True
        ) as admin:
            await admin.execute("ANALYZE traces")
        await bind_project(db, project)
        counts = await read_trace_counts(db, project, groups, environment)
        assert counts == ([30, 30] if environment is None else [20, 20])

        statement = _TRACE_COUNTS[environment is not None]
        plan = (
            await db.execute(
                text(f"EXPLAIN (FORMAT JSON) {statement.text}").bindparams(
                    *statement._bindparams.values()
                ),
                _params(project, groups, environment),
            )
        ).scalar_one()

    def estimates(node: dict[str, Any]) -> list[int]:
        own = [node["Plan Rows"]] if node.get("Relation Name") == "traces" else []
        return own + [rows for child in node.get("Plans", []) for rows in estimates(child)]

    # Each range holds 30 of the 20 000 traces. Sized at a ninth of the table each, the ranges
    # made reading the whole table look cheaper than reading them from the index.
    [per_range] = estimates(plan[0]["Plan"])
    assert per_range < 500

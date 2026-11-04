"""The model-filtered trace list reads the spans of its window, planned on a true row estimate.

Failure mode: the filter is an `EXISTS` over the trace's spans. Without a bound on the spans'
start, a hash plan reads every span of the model the project ever stored; and with Postgres
multiplying the project's and the model's shares of the whole table, a busy project's common
model looks rare, which is the plan it picks. That was half of the database time of the trace
list load test. Migration 1038 adds statistics on (project_id, model) together, and the filter
bounds the spans it reads by the window's start.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.trace_filters import TraceListFilters
from app.api.window import TimeWindow
from app.db.models import Trace
from tests.db import schema_probe as probe

ORG_A = uuid.UUID("10000000-0000-4000-8000-000000001038")
PROJECT_A = uuid.UUID("20000000-0000-4000-8000-000000001038")
ORG_B = uuid.UUID("10000000-0000-4000-8000-000000002038")
PROJECT_B = uuid.UUID("20000000-0000-4000-8000-000000002038")
END = datetime(2026, 10, 1, tzinfo=UTC)

# Project A: 2 000 spans of its model and 2 000 of another. Project B, the busy neighbour:
# 60 000 spans of a third model. Taken apart, A is 6 % of the table and its model 3 %, so the
# planner expects about 125 rows for A's 2 000.
SEED = """
INSERT INTO traces (project_id, trace_id, started_at, ended_at)
SELECT %(project)s, md5(%(project)s::text || n), %(end)s::timestamptz - n * interval '1 minute',
       %(end)s::timestamptz - n * interval '1 minute' + interval '1 second'
FROM generate_series(1, %(count)s) AS n
"""
SEED_SPANS = """
INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, started_at, ended_at, model)
SELECT %(project)s, md5(%(project)s::text || n), left(md5(n::text), 16), 'llm', 'call', 'ok',
       %(end)s::timestamptz - n * interval '1 minute',
       %(end)s::timestamptz - n * interval '1 minute' + interval '1 second',
       CASE WHEN n %% 2 = 0 THEN %(model)s ELSE %(other)s END
FROM generate_series(1, %(count)s) AS n
"""


def _seed() -> None:
    with probe.admin() as connection:
        probe.seed_project(connection, ORG_A, PROJECT_A)
        connection.execute(
            "INSERT INTO organizations (id, name, slug) VALUES (%s, 'Org B', 'org-b-1038')",
            (ORG_B,),
        )
        connection.execute(
            "INSERT INTO projects (id, org_id, name, slug) VALUES (%s, %s, 'B', 'b')",
            (PROJECT_B, ORG_B),
        )
        for project, count, model, other in (
            (PROJECT_A, 4_000, "model-a", "model-other"),
            (PROJECT_B, 60_000, "model-b", "model-b"),
        ):
            params = {"project": project, "end": END, "count": count}
            connection.execute(SEED, params)
            connection.execute(SEED_SPANS, {**params, "model": model, "other": other})
        connection.execute("ANALYZE spans")


def _plan(sql: str) -> list[str]:
    with probe.admin() as connection:
        return [row[0] for row in connection.execute(f"EXPLAIN {sql}").fetchall()]


def test_a_projects_model_is_estimated_from_the_pair() -> None:
    _seed()
    plan = _plan(f"SELECT * FROM spans WHERE project_id = '{PROJECT_A}' AND model = 'model-a'")
    estimate = re.search(r"rows=(\d+)", plan[0])
    assert estimate is not None, plan
    assert 1_000 <= int(estimate.group(1)) <= 4_000, plan


async def test_the_model_filter_reads_spans_from_the_window_start(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    window = TimeWindow(END - timedelta(hours=24), END)
    async with session_factory() as session:
        query = await TraceListFilters(model="model-a").narrow(
            session, select(Trace), PROJECT_A, window
        )
    sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    exists = sql[sql.index("EXISTS") :]
    assert re.search(r"spans\.started_at >= '2026-09-30 00:00:00\+00:00'", exists), sql

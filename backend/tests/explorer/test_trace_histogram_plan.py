"""The 30-day trace histogram is planned for the handful of days it returns.

Failure mode: without statistics on the day bucket, Postgres guesses one group per few traces,
sorts every trace of the window (spilling to disk on a busy project) and misses the 150 ms budget
in docs/performance.md. Migration 1017 adds expression statistics on exactly the bucket the
histogram groups by; this pins that the query still uses that expression, so the planner's group
estimate stays near the number of days.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

from app.db.models import Trace
from app.explorer.trace_histogram import day_bucket
from tests.db import schema_probe as probe

ORG_ID = uuid.UUID("10000000-0000-4000-8000-000000001017")
PROJECT_ID = uuid.UUID("20000000-0000-4000-8000-000000001017")
DAYS = 30
TRACES = 3_000


def _seed() -> None:
    end = datetime(2026, 10, 1, tzinfo=UTC)
    step = timedelta(days=DAYS) / TRACES
    with probe.admin() as connection:
        probe.seed_project(connection, ORG_ID, PROJECT_ID)
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO traces (project_id, trace_id, started_at, ended_at) "
                "VALUES (%s, %s, %s, %s)",
                [
                    (PROJECT_ID, f"{index:032x}", end - step * index, end - step * index)
                    for index in range(TRACES)
                ],
            )
        connection.execute("ANALYZE traces")


def test_the_day_histogram_expects_about_one_group_per_day() -> None:
    _seed()
    bucket = day_bucket()
    query = (
        select(bucket, func.count())
        .where(Trace.project_id == PROJECT_ID)
        .group_by(bucket)
        .order_by(bucket)
    )
    sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    with probe.admin() as connection:
        plan = connection.execute(f"EXPLAIN {sql}").fetchall()
    estimate = re.search(r"rows=(\d+)", plan[0][0])
    assert estimate is not None, plan
    assert int(estimate.group(1)) <= DAYS + 10, plan

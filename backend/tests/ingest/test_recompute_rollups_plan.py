"""The trace-total recompute never reads spans through a `request_hash` index, whatever the
statistics say.

Failure mode: the retry ranking filters on `request_hash IS NOT NULL`, which is the predicate of
the partial index `spans_project_request_hash_started_idx`. When the last ANALYZE saw few spans
with a hash (a seeded project before its gateway traffic), the planner takes that index as
nearly empty and walks every gateway span of the project through it on each span write (69 ms
instead of 0.2 ms in the Wave 0 gateway run). Reading the touched traces' spans once, by primary
key, and ranking the retries from that set leaves the partial index out of the plan.
"""

import uuid
from datetime import UTC, datetime

import psycopg

from app.ingest.store import _RECOMPUTE_ROLLUPS
from tests.db import schema_probe as probe

ORG_ID = uuid.UUID("10000000-0000-4000-8000-000000000e09")
PROJECT_ID = uuid.UUID("20000000-0000-4000-8000-000000000e09")
END = datetime(2026, 10, 1, tzinfo=UTC)

SEED_TRACES = """
INSERT INTO traces (project_id, trace_id, started_at, ended_at)
SELECT %(project)s, lpad(n::text, 32, '0'), %(end)s::timestamptz - n * interval '1 second',
       %(end)s::timestamptz - n * interval '1 second' + interval '1 second'
FROM generate_series(%(first)s::int, %(last)s::int) AS n
"""
SEED_SPANS = """
INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, started_at, ended_at,
                   request_hash)
SELECT %(project)s, lpad(n::text, 32, '0'), left(md5(n::text), 16), 'llm', 'chat', 'ok',
       %(end)s::timestamptz - n * interval '1 second',
       %(end)s::timestamptz - n * interval '1 second' + interval '1 second',
       CASE WHEN %(hashed)s THEN md5((n %% 500)::text) END
FROM generate_series(%(first)s::int, %(last)s::int) AS n
"""
SQL = _RECOMPUTE_ROLLUPS.text.replace(":project_id", "%(project_id)s").replace(
    ":trace_ids", "%(trace_ids)s"
)


def _seed(connection: psycopg.Connection, first: int, last: int, *, hashed: bool) -> None:
    params = {"project": PROJECT_ID, "end": END, "first": first, "last": last, "hashed": hashed}
    connection.execute(SEED_TRACES, params)
    connection.execute(SEED_SPANS, params)


def test_the_recompute_skips_the_request_hash_index_on_stale_statistics() -> None:
    with probe.admin() as connection:
        probe.seed_project(connection, ORG_ID, PROJECT_ID)
        # SDK spans carry no hash; the statistics are gathered on them alone, as after a seed.
        _seed(connection, 1, 2_000, hashed=False)
        connection.execute("ANALYZE spans")
        # Then the gateway writes its spans, every one with a hash, before the next analyze.
        _seed(connection, 2_001, 12_000, hashed=True)
        plan = "\n".join(
            str(line)
            for (line,) in connection.execute(
                f"EXPLAIN {SQL}",
                {"project_id": PROJECT_ID, "trace_ids": [f"{12_000:0>32}"]},
            ).fetchall()
        )
    # Index or Bitmap scan, by any index but the `request_hash` one (the only index on that column).
    assert "spans_project_request_hash_started_idx" not in plan, plan

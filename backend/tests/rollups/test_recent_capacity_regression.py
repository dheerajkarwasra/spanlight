"""A newly busy tenant must settle even when statistics still describe another tenant."""

from datetime import UTC, datetime, timedelta
from time import monotonic

import pytest

from app.config import Settings
from app.rollups.compute import floor_hour
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects, seed_llm_spans
from tests.rollups.recent_support import admit_at, run_recent, state

pytestmark = pytest.mark.serial

# Exactly 1,000 models per hour; hashing random trace IDs could leave a model bucket empty.
_SEED_ERRORS = """
    WITH ids AS (
        SELECT n, md5(random()::text || n::text) AS trace_id FROM generate_series(1, 10000) n
    ), new_traces AS (
        INSERT INTO traces (project_id, trace_id, started_at, ended_at)
        SELECT :p, trace_id, :h, :h + interval '1 second' FROM ids
    )
    INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, started_at, ended_at,
                       provider, model, error_class, input_tokens, output_tokens)
    SELECT :p, trace_id, left(trace_id, 16), 'llm', 'call', 'error', :h,
           :h + interval '1 second', 'openai', 'model-' || (n % 1000), 'timeout', 10, 5
    FROM ids
"""


async def test_new_tenant_error_spike_settles_six_hours_with_prior_tenant_statistics(
    session_factory: SessionFactory, settings: Settings
) -> None:
    prior, project = await new_projects(session_factory, 2)
    now = datetime.now(UTC)
    old = floor_hour(now) - timedelta(days=3)
    await admit_at(session_factory, project, old)
    await seed_llm_spans(session_factory, prior, 6000, started_at=now)
    # Real statistics deliberately precede a new tenant's burst; no ANALYZE rescues its work.
    await execute(session_factory, "ANALYZE spans; ANALYZE traces")
    for hour in (
        old,
        old + timedelta(hours=1),
        old + timedelta(hours=2),
        floor_hour(now) - timedelta(hours=2),
        floor_hour(now) - timedelta(hours=1),
        floor_hour(now),
    ):
        await execute(session_factory, _SEED_ERRORS, {"p": project, "h": hour})
    started = monotonic()
    await run_recent(session_factory, settings, now)
    elapsed = monotonic() - started
    assert (await state(session_factory, project))["next_closed_hour"] == old + timedelta(hours=3)
    assert await fetch(
        session_factory,
        "SELECT sum(span_count) n,count(DISTINCT bucket_start) hours"
        " FROM span_rollups_hourly WHERE project_id=:p",
        {"p": project},
    ) == [{"n": 60000, "hours": 6}]
    assert await fetch(
        session_factory,
        "SELECT sum(traces) n FROM trace_rollups_hourly WHERE project_id=:p",
        {"p": project},
    ) == [{"n": 60000}]
    assert await fetch(
        session_factory,
        "SELECT sum(errors) n,count(*) groups FROM error_rollups_hourly WHERE project_id=:p",
        {"p": project},
    ) == [{"n": 60000, "groups": 6000}]
    print("CALIBRATION stale_prior_tenant_six_hours_seconds=", elapsed)

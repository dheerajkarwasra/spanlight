"""Scoped per-day cardinality calibration; this is not the final mixed workload campaign."""

import json
from datetime import timedelta
from decimal import Decimal
from time import monotonic

import pytest
from sqlalchemy import text

from app.attribution.aggregate_sql import grouped_cte
from app.config import Settings
from app.db.rls import bind_project
from app.end_users.days import midnight
from tests.attribution.daily_support import child, dispatch, run, state, today
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects

pytestmark = pytest.mark.serial


@pytest.mark.parametrize("count", [5000, 50000])
async def test_daily_transaction_capacity_preserves_one_day_and_folded_totals(
    session_factory: SessionFactory,
    settings: Settings,
    count: int,
) -> None:
    [project] = await new_projects(session_factory, 1)
    day = today() - timedelta(days=7)
    params = {"project": project, "started": midnight(day) + timedelta(hours=12), "count": count}
    await execute(
        session_factory,
        "UPDATE projects SET promoted_metadata_keys=ARRAY['customer'] WHERE id=:project",
        params,
    )
    await execute(
        session_factory,
        """INSERT INTO traces(project_id,trace_id,name,metadata,started_at,ended_at)
        SELECT :project,md5(n::text),'load',jsonb_build_object('customer',n::text),:started,:started
        FROM generate_series(1,:count) n""",
        params,
    )
    await execute(
        session_factory,
        """INSERT INTO spans(project_id,trace_id,span_id,kind,name,status,
                          started_at,ended_at,cost_usd)
        SELECT :project,md5(n::text),left(md5(n::text),16),'llm','call','ok',:started,:started,0.01
        FROM generate_series(1,:count) n""",
        params,
    )
    enabled = settings.model_copy(update={"attribution_enabled": True})
    await dispatch(session_factory, enabled)
    job = await child(session_factory)
    started = monotonic()
    await run(session_factory, enabled, job)
    elapsed = monotonic() - started
    rows = await fetch(
        session_factory,
        "SELECT sum(cost_usd) cost,sum(traces) traces,count(*) groups FROM "
        "attribution_rollups_daily WHERE project_id=:project AND group_kind='metadata'",
        params,
    )
    assert rows == [{"cost": Decimal(count) / 100, "traces": count, "groups": 1001}]
    assert (await state(session_factory, project))["next_day"] == day + timedelta(days=1)
    assert elapsed < 5
    print("CALIBRATION daily_source_traces=", count, "promoted_groups=", count, "seconds=", elapsed)

    async with session_factory() as db:
        await bind_project(db, project)
        plan = await db.scalar(
            text(
                "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) WITH "
                + grouped_cte("metadata", by_day=True, with_environment=False)
                + " SELECT * FROM grouped"
            ),
            {
                "project_id": project,
                "start": midnight(day),
                "end": midnight(day + timedelta(days=1)),
                "key": "customer",
            },
        )
        print("SOURCE_PLAN", count, json.dumps(plan, default=str))

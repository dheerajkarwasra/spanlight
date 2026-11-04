"""The parameterized trace lookup retains tenant, NULL-environment and source-loss semantics."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.rls import bind_project
from app.rollups.compute import compute_rollups, floor_hour
from app.rollups.coverage import lock_aggregate_sources, mark_trace_source_loss
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects


async def test_same_ids_null_environment_and_cascade_keep_exact_error_groups(
    session_factory: SessionFactory,
) -> None:
    a, b = await new_projects(session_factory, 2)
    hour = floor_hour(datetime.now(UTC)) - timedelta(hours=6)
    for project, environment in ((a, None), (b, "production")):
        await execute(
            session_factory,
            "INSERT INTO traces(project_id,trace_id,started_at,ended_at,environment)"
            " VALUES(:p,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',:h,:h,:e)",
            {"p": project, "h": hour, "e": environment},
        )
        await execute(
            session_factory,
            "INSERT INTO spans(project_id,trace_id,span_id,kind,name,status,started_at,"
            "ended_at,provider,model,error_class,rate_limit_kind,attributes)"
            " VALUES(:p,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','bbbbbbbbbbbbbbbb','llm','call',"
            "'error',:h,:h,'openai','m','rate_limit','tokens',"
            "jsonb_build_object('provider.retry_after_s',2))",
            {"p": project, "h": hour},
        )
    await execute(
        session_factory,
        "INSERT INTO spans(project_id,trace_id,span_id,kind,name,status,started_at,"
        "ended_at,provider,model,error_class,rate_limit_kind,attributes)"
        " VALUES(:p,'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','cccccccccccccccc','llm','call',"
        "'error',:h,:h,'openai','m','rate_limit','tokens',"
        "jsonb_build_object('provider.retry_after_s',NULL))",
        {"p": a, "h": hour},
    )
    for project in (a, b):
        async with session_factory() as db:
            await compute_rollups(db, project, hour, hour + timedelta(hours=1))
            await db.commit()
    assert await fetch(
        session_factory,
        "SELECT environment,errors,with_retry_after FROM error_rollups_hourly WHERE project_id=:p",
        {"p": a},
    ) == [{"environment": None, "errors": 2, "with_retry_after": 1}]
    assert await fetch(
        session_factory,
        "SELECT environment,errors,with_retry_after FROM error_rollups_hourly WHERE project_id=:p",
        {"p": b},
    ) == [{"environment": "production", "errors": 1, "with_retry_after": 1}]
    original = await fetch(
        session_factory, "SELECT * FROM error_rollups_hourly ORDER BY project_id"
    )
    async with session_factory() as db:
        await lock_aggregate_sources(db, a)
        await mark_trace_source_loss(db, a, ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"], "erasure")
        await db.execute(
            text(
                "DELETE FROM traces WHERE project_id=:p"
                " AND trace_id='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'"
            ),
            {"p": a},
        )
        await db.commit()
        await compute_rollups(db, a, hour, hour + timedelta(hours=1))
        await db.commit()
    assert (
        await fetch(session_factory, "SELECT * FROM error_rollups_hourly ORDER BY project_id")
        == original
    )
    assert await fetch(session_factory, "SELECT * FROM spans WHERE project_id=:p", {"p": a}) == []
    assert await fetch(
        session_factory,
        "SELECT condeferrable,convalidated FROM pg_constraint"
        " WHERE conrelid='spans'::regclass AND confrelid='traces'::regclass",
    ) == [{"condeferrable": False, "convalidated": True}]
    async with session_factory() as db:
        await bind_project(db, a)
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                await db.execute(
                    text(
                        "INSERT INTO spans(project_id,trace_id,span_id,kind,name,status,"
                        "started_at,ended_at) VALUES(:p,'dddddddddddddddddddddddddddddddd',"
                        "'eeeeeeeeeeeeeeee','llm','call','error',:h,:h)"
                    ),
                    {"p": a, "h": hour},
                )

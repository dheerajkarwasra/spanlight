"""Generated source-loss positions preserve snapshots without freezing adjacent complete hours."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import text

from app.db.rls import bypass_rls
from app.end_users.erasure_job import erase_end_user
from app.rollups.compute import compute_rollups
from tests.attribution.support import SessionFactory, SpanSeed, TraceSeed, seed_traces
from tests.helpers import create_workspace


@given(minute=st.integers(min_value=0, max_value=59), passes=st.integers(min_value=1, max_value=3))
@settings(
    max_examples=8, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
async def test_erasure_preserves_null_dimension_snapshot_and_allows_adjacent_refresh(
    browser_factory: Any, session_factory: SessionFactory, minute: int, passes: int
) -> None:
    workspace = await create_workspace(
        await browser_factory(f"coverage-{uuid.uuid4()}@example.com")
    )
    project = uuid.UUID(workspace.project_id)
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=8)
    started = hour + timedelta(minutes=minute)
    adjacent = hour + timedelta(hours=1)
    async with session_factory() as db:
        await bypass_rls(db)
        await seed_traces(
            db,
            project,
            {
                "erased": TraceSeed(
                    started,
                    (SpanSeed("llm", started, Decimal(1), 10, 2),),
                    external_user_id="erased",
                ),
                "kept": TraceSeed(started, (SpanSeed("llm", started, Decimal(2), 10, 2),)),
            },
        )
        await db.execute(
            text("UPDATE spans SET model = NULL WHERE project_id = :id"), {"id": project}
        )
        await compute_rollups(db, project, hour, adjacent)
        await db.commit()
    await erase_end_user(session_factory, project_id=project, external_user_id="erased", counts={})
    async with session_factory() as db:
        await bypass_rls(db)
        await seed_traces(
            db,
            project,
            {
                "new": TraceSeed(adjacent, (SpanSeed("llm", adjacent, Decimal(4), 10, 2),)),
            },
        )
        for _ in range(passes):
            await compute_rollups(db, project, hour, adjacent + timedelta(hours=1))
        await db.commit()
    base = f"/api/v1/projects/{project}/metrics"
    lost = await workspace.owner.get(
        f"{base}/models",
        params={
            "from": hour.isoformat(),
            "to": adjacent.isoformat(),
        },
    )
    assert lost.status_code == 200
    assert lost.headers["Aggregate-Status"] == "unavailable"
    assert len(lost.json()) == 1
    assert lost.json()[0]["model"] is None
    assert lost.json()[0]["calls"] == 2
    assert Decimal(lost.json()[0]["cost_usd"]) == Decimal(3)
    safe = await workspace.owner.get(
        f"{base}/overview",
        params={
            "from": adjacent.isoformat(),
            "to": (adjacent + timedelta(hours=1)).isoformat(),
        },
    )
    assert safe.status_code == 200
    assert safe.json()["current"]["aggregate_status"] == "complete"
    assert safe.json()["current"]["llm_calls"] == 1
    assert Decimal(safe.json()["current"]["cost_usd"]) == Decimal(4)

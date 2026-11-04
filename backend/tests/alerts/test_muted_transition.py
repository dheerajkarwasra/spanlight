"""A muted rule still changes state and records its event; it only notifies nobody.

Failure mode pinned here: the "notification muted" log line passing a reserved structlog key, so
every transition of a muted rule raised and rolled back, and the rule never fired or resolved.
"""

import uuid
from datetime import UTC, datetime, timedelta

from app.alerts.jobs import evaluate_all
from app.config import Settings
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects, seed_llm_spans


async def test_muted_rule_fires_and_records_its_event(
    session_factory: SessionFactory, settings: Settings
) -> None:
    (project,) = await new_projects(session_factory, 1)
    rule_id = uuid.uuid4()
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    await execute(
        session_factory,
        "INSERT INTO alert_rules (id, project_id, name, kind, metric, window_minutes, comparator, "
        "threshold, muted_until) VALUES (:id, :project_id, 'Any call', 'threshold', 'llm_calls', "
        "5, 'gt', 0, :muted_until)",
        {"id": rule_id, "project_id": project, "muted_until": now + timedelta(days=1)},
    )
    await seed_llm_spans(session_factory, project, 1, started_at=now - timedelta(seconds=30))

    run = await evaluate_all(session_factory, settings, now=now)

    assert (run.errors, run.projects_failed) == (0, 0)
    params = {"id": rule_id}
    (state,) = await fetch(
        session_factory, "SELECT state FROM alert_states WHERE rule_id = :id", params
    )
    assert state["state"] == "firing"
    events = await fetch(
        session_factory, "SELECT started_at FROM alert_events WHERE rule_id = :id", params
    )
    assert events == [{"started_at": now}]

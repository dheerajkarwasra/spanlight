"""Migration 0705 adds `error_calls` to the `alert_metric` enum, and can be undone.

The downgrade is lossy for one metric: rules that count failed calls are deleted (in every
project, whatever row-level security hides), because the enum it restores has no such value. Every
other rule, and the CHECK that ties `spend` to budget rules, survives the round trip.

The round trip downgrades the session's shared database and leaves it at head.
"""

import uuid

import psycopg
import pytest

from app.db.migrations import downgrade_to, head_revision, upgrade_to_head
from tests.db import schema_probe as probe

ORG_ID = uuid.UUID("10000000-0000-4000-8000-000000000705")
PROJECT_ID = uuid.UUID("20000000-0000-4000-8000-000000000705")
METRICS_0303 = ["error_rate", "p95_ms", "ttft_p95_ms", "cost_usd", "llm_calls", "tokens", "spend"]
SPEND_CHECK = "alert_rules_spend_for_kind"


def _metrics(database_url: str) -> list[object]:
    rows = probe.query(
        database_url,
        "SELECT e.enumlabel FROM pg_enum AS e JOIN pg_type AS t ON t.oid = e.enumtypid "
        "WHERE t.typname = 'alert_metric' ORDER BY e.enumsortorder",
    )
    return [row[0] for row in rows]


def _rule(connection: psycopg.Connection, metric: str) -> uuid.UUID:
    rule_id = uuid.uuid4()
    connection.execute(
        "INSERT INTO alert_rules (id, project_id, name, kind, metric, comparator, threshold, "
        "window_minutes) VALUES (%s, %s, 'rule', 'threshold', %s, 'gt', 0, 60)",
        (rule_id, PROJECT_ID, metric),
    )
    return rule_id


def _rules(connection: psycopg.Connection) -> set[uuid.UUID]:
    rows = connection.execute("SELECT id FROM alert_rules WHERE project_id = %s", (PROJECT_ID,))
    return {row[0] for row in rows}


def test_0705_round_trip(app_database_url: str) -> None:
    assert probe.current_revision(app_database_url) == head_revision()
    assert _metrics(app_database_url) == [*METRICS_0303, "error_calls"]
    with probe.admin() as connection:
        probe.seed_project(connection, ORG_ID, PROJECT_ID)
        failed_calls, latency = _rule(connection, "error_calls"), _rule(connection, "p95_ms")

    downgrade_to(app_database_url, "0704")
    try:
        assert probe.current_revision(app_database_url) == "0704"
        assert _metrics(app_database_url) == METRICS_0303
        assert probe.constraints(app_database_url, "alert_rules")[SPEND_CHECK] == (
            "CHECK (((metric = 'spend'::alert_metric) = (kind = 'budget'::alert_rule_kind)))",
            True,
        )
        with probe.admin() as connection:
            assert _rules(connection) == {latency}
            with pytest.raises(psycopg.errors.InvalidTextRepresentation):
                _rule(connection, "error_calls")
    finally:
        upgrade_to_head(app_database_url)

    assert _metrics(app_database_url) == [*METRICS_0303, "error_calls"]
    with probe.admin() as connection:
        assert failed_calls not in _rules(connection)
        _rule(connection, "error_calls")

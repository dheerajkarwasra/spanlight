"""Migrations 1010 to 1012 add the item copies, the dataset lock and the source-trace index.

1010 gives `experiment_items` a copy of the dataset item it was scored against (`input`,
`expected_output`, `item_metadata`), all or nothing by `experiment_items_copy_check`. 1011 adds
`datasets.locked_at` and the `dataset.lock` / `dataset.unlock` audit actions. 1012 indexes
`dataset_items.source_trace_id`. Each can be undone.

Every test here leaves the session's shared database at head.
"""

import json
import uuid
from collections.abc import Iterator

import psycopg
import pytest

from app.db.migrations import downgrade_to, head_revision, upgrade_to_head
from tests.db import schema_probe as probe

BELOW = "1009"
ORG_ID = uuid.UUID("10000000-0000-4000-8000-000000001010")
PROJECT_ID = uuid.UUID("20000000-0000-4000-8000-000000001010")
DATASET_ID = uuid.UUID("30000000-0000-4000-8000-000000001010")
EXPERIMENT_ID = uuid.UUID("40000000-0000-4000-8000-000000001010")
RUN_ID = uuid.UUID("50000000-0000-4000-8000-000000001010")

COPY_COLUMNS = {
    "input": ("jsonb", "YES", None, None, None),
    "expected_output": ("jsonb", "YES", None, None, None),
    "item_metadata": ("jsonb", "YES", None, None, None),
}
COPY_CHECK = "experiment_items_copy_check"
SOURCE_TRACE_INDEX = "dataset_items_source_trace_idx"


@pytest.fixture
def back_to_head(app_database_url: str) -> Iterator[None]:
    yield
    upgrade_to_head(app_database_url)
    assert probe.current_revision(app_database_url) == head_revision()


def _assert_at_head(database_url: str) -> None:
    assert probe.current_revision(database_url) == "1012" == head_revision()
    items = probe.columns(database_url, "experiment_items")
    assert {name: items.get(name) for name in COPY_COLUMNS} == COPY_COLUMNS
    assert probe.columns(database_url, "datasets")["locked_at"][:2] == ("timestamptz", "YES")
    definition, validated = probe.constraints(database_url, "experiment_items")[COPY_CHECK]
    assert validated, definition
    assert probe.indexes(database_url, "dataset_items")[SOURCE_TRACE_INDEX] == (
        f"CREATE INDEX {SOURCE_TRACE_INDEX} ON public.dataset_items USING btree (source_trace_id) "
        "WHERE (source_trace_id IS NOT NULL)"
    )


def _assert_at_1009(database_url: str) -> None:
    assert probe.current_revision(database_url) == BELOW
    assert not COPY_COLUMNS.keys() & probe.columns(database_url, "experiment_items").keys()
    assert "locked_at" not in probe.columns(database_url, "datasets")
    assert COPY_CHECK not in probe.constraints(database_url, "experiment_items")
    assert SOURCE_TRACE_INDEX not in probe.indexes(database_url, "dataset_items")


def test_migrations_1010_to_1012_round_trip(app_database_url: str, back_to_head: None) -> None:
    _assert_at_head(app_database_url)
    downgrade_to(app_database_url, BELOW)
    _assert_at_1009(app_database_url)
    upgrade_to_head(app_database_url)
    _assert_at_head(app_database_url)


def _seed_run(connection: psycopg.Connection) -> None:
    probe.seed_project(connection, ORG_ID, PROJECT_ID)
    connection.execute(
        "INSERT INTO datasets (id, project_id, name) VALUES (%s, %s, 'golden')",
        (DATASET_ID, PROJECT_ID),
    )
    connection.execute(
        "INSERT INTO experiments (id, project_id, name, dataset_id, config) "
        "VALUES (%s, %s, 'exp', %s, '{}')",
        (EXPERIMENT_ID, PROJECT_ID, DATASET_ID),
    )
    connection.execute(
        "INSERT INTO experiment_runs (id, experiment_id, number, target) VALUES (%s, %s, 1, '{}')",
        (RUN_ID, EXPERIMENT_ID),
    )


def _insert_item(
    connection: psycopg.Connection,
    input: object = None,
    expected_output: object = None,
    item_metadata: object = None,
) -> None:
    def as_json(value: object) -> str | None:
        return None if value is None else json.dumps(value)

    connection.execute(
        "INSERT INTO experiment_items "
        "(run_id, dataset_item_id, input, expected_output, item_metadata) "
        "VALUES (%s, %s, %s::jsonb, %s::jsonb, %s::jsonb)",
        (
            RUN_ID,
            uuid.uuid4(),
            as_json(input),
            as_json(expected_output),
            as_json(item_metadata),
        ),
    )


def test_copy_check_refuses_a_partial_copy() -> None:
    with probe.admin() as connection:
        _seed_run(connection)
        with pytest.raises(psycopg.errors.CheckViolation, match=COPY_CHECK):
            _insert_item(connection, input={"q": "hi"})
        with pytest.raises(psycopg.errors.CheckViolation, match=COPY_CHECK):
            _insert_item(connection, expected_output="hello", item_metadata={})
        # A run made before copies existed: nothing copied.
        _insert_item(connection)
        # A full copy, with and without an expected output.
        _insert_item(connection, input={"q": "hi"}, item_metadata={})
        _insert_item(connection, input={"q": "hi"}, expected_output="hello", item_metadata={})


def test_audit_check_accepts_dataset_lock_actions(
    app_database_url: str, back_to_head: None
) -> None:
    with probe.admin() as connection:
        probe.seed_project(connection, ORG_ID, PROJECT_ID)
        for action in ("dataset.lock", "dataset.unlock"):
            connection.execute(
                "INSERT INTO audit_events (id, org_id, action, target_type, target_id) "
                "VALUES (gen_random_uuid(), %s, %s, 'dataset', 'x')",
                (ORG_ID, action),
            )

    downgrade_to(app_database_url, "1010")
    remaining = probe.query(
        app_database_url,
        "SELECT count(*) FROM audit_events WHERE action IN ('dataset.lock', 'dataset.unlock')",
    )
    assert remaining == [(0,)]

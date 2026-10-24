"""The attribution CSV export: the request body, the file the job writes, and its limits.

The job tests seed the three-day fixture of `tests.attribution.support` and run `create_export`
as the worker would, against an in-memory object store. The API tests run the app with object
storage configured (nothing connects to it) so `POST .../exports` accepts the request.
"""

import csv
import io
import json
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from app.api.schemas.attribution import AttributionBucketOut, AttributionRowOut
from app.config import Settings
from app.core.observability import FEATURE_USED
from app.db.rls import bypass_rls
from app.exports import attribution_job
from app.exports import jobs as export_jobs
from app.exports.attribution_job import ATTRIBUTION_CSV_COLUMNS, attribution_csv_line
from app.jobs.context import TaskContext
from app.jobs.queue import ClaimedJob, claim_next, enqueue
from tests.attribution.support import DAYS, WINDOW_END, WINDOW_START, seed_attribution_fixture
from tests.conftest import _browsers, _running
from tests.gateway_support import metric_delta
from tests.helpers import Browser, Workspace, create_workspace
from tests.jobs.bypass_support import SessionFactory, execute, fetch, new_projects, seed_row
from tests.jobs.test_export_tenancy import StreamingMemoryStore

D0, D1, D2 = DAYS
D3 = D2 + timedelta(days=1)

# A rollup `other` row for D0, as the refresh writes one past the daily cap.
_INSERT_OTHER = text(
    """
    INSERT INTO attribution_rollups_daily
        (project_id, day, environment, group_kind, group_key, bucket, group_value, traces,
         llm_calls, errors, unpriced_calls, input_tokens, output_tokens, cost_usd, folded_values)
    VALUES (:project_id, :day, 'prod', 'metadata', 'customer', 'other', NULL, 3, 3, 0, 0, 10, 5,
            0.02, 7)
    """
)
# The rollup rows of one key over the fixture's window, written by the refresh's own CTE.
_INSERT_ROLLUPS = text(
    """
    INSERT INTO attribution_rollups_daily
        (project_id, day, environment, group_kind, group_key, bucket, group_value, traces,
         llm_calls, errors, unpriced_calls, input_tokens, output_tokens, cost_usd, retries,
         retry_cost_usd)
    SELECT project_id, (started_at AT TIME ZONE 'UTC')::date, environment, 'metadata',
           :key, CASE WHEN metadata ->> :key IS NULL THEN 'none' ELSE 'value' END,
           metadata ->> :key, count(*), 0, 0, 0, sum(input_tokens), sum(output_tokens),
           sum(cost_usd), NULL, NULL
    FROM traces
    WHERE project_id = :project_id AND started_at >= :start AND started_at < :end
    GROUP BY project_id, (started_at AT TIME ZONE 'UTC')::date, environment, metadata ->> :key
    """
)


def _figures(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "traces": 1,
        "llm_calls": 2,
        "errors": 0,
        "unpriced_calls": 0,
        "input_tokens": 10,
        "output_tokens": 5,
        "cost_usd": Decimal("0.5"),
        "retries": None,
        "retry_cost_usd": None,
    }
    return {**base, **overrides}


def _cells(line: bytes) -> list[str]:
    return next(csv.reader(io.StringIO(line.decode())))


def _rows(body: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(body.decode())))


# --- the encoder -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ['=HYPERLINK("http://x")', "+1", "-2+3", "@SUM(A1)", "\tx", "\rx", "=" + "A" * 255],
)
def test_every_cell_is_formula_safe(value: str) -> None:
    row = AttributionRowOut(**_figures(), value=value, share_of_cost=None)
    cells = _cells(attribution_csv_line("metadata", "customer", "value", row, value=row.value))
    assert cells[2] == f"'{value}"
    assert len(cells) == len(ATTRIBUTION_CSV_COLUMNS)


def test_unknowns_are_empty_cells_and_money_is_a_decimal_string() -> None:
    other = AttributionBucketOut(**_figures(cost_usd=Decimal("0.00000001")), folded_values=7)
    cells = _cells(attribution_csv_line("metadata", "customer", "other", other, folded_values=7))
    assert dict(zip(ATTRIBUTION_CSV_COLUMNS, cells, strict=True)) == {
        "group_by": "metadata",
        "key": "customer",
        "value": "",
        "bucket": "other",
        "traces": "1",
        "llm_calls": "2",
        "errors": "0",
        "unpriced_calls": "0",
        "input_tokens": "10",
        "output_tokens": "5",
        "cost_usd": "0.00000001",
        "retries": "",
        "retry_cost_usd": "",
        "folded_values": "7",
    }


# --- the job -----------------------------------------------------------------------------------


async def _queued_attribution_export(
    session_factory: SessionFactory, project_id: uuid.UUID, **request: Any
) -> uuid.UUID:
    row = await seed_row(session_factory, "exports", project_id)
    stored = {
        "group_by": "metadata",
        "key": "customer",
        "filters": {"from": WINDOW_START.isoformat(), "to": WINDOW_END.isoformat()},
        **request,
    }
    await execute(
        session_factory,
        "UPDATE exports SET kind = 'attribution', format = 'csv', "
        "filters = CAST(:filters AS jsonb) WHERE id = :id",
        {"id": row["id"], "filters": json.dumps(stored)},
    )
    return uuid.UUID(str(row["id"]))


async def _run_export(
    session_factory: SessionFactory, settings: Settings, project_id: uuid.UUID, export_id: uuid.UUID
) -> dict[str, Any]:
    payload = {"export_id": str(export_id), "project_id": str(project_id)}
    async with session_factory() as db:
        await enqueue(db, "create_export", payload, max_attempts=1)
        await db.commit()
        job = await claim_next(db)
    assert isinstance(job, ClaimedJob)
    context = TaskContext(job=job, settings=settings, session_factory=session_factory)
    await export_jobs.run_create_export(context, payload)
    (row,) = await fetch(
        session_factory,
        "SELECT status::text AS status, storage_key, row_count, error_code FROM exports "
        "WHERE id = :id",
        {"id": export_id},
    )
    return dict(row)


async def _promote_and_roll_up(
    session_factory: SessionFactory, project_id: uuid.UUID, key: str, *, other: bool
) -> None:
    params = {"project_id": project_id, "key": key, "start": WINDOW_START, "end": WINDOW_END}
    async with session_factory() as db:
        await bypass_rls(db)
        await db.execute(
            text("UPDATE projects SET promoted_metadata_keys = ARRAY[:key] WHERE id = :id"),
            {"key": key, "id": project_id},
        )
        await db.execute(_INSERT_ROLLUPS, params)
        if other:
            await db.execute(_INSERT_OTHER, {"project_id": project_id, "day": D0})
        await db.commit()


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> StreamingMemoryStore:
    memory = StreamingMemoryStore()
    monkeypatch.setattr(export_jobs, "get_object_store", lambda _settings: memory)
    return memory


async def test_csv_columns_and_order_with_none_and_other_last(
    session_factory: SessionFactory, settings: Settings, store: StreamingMemoryStore
) -> None:
    """Over the rollup path: values by cost descending (unknown last), then `none`, then `other`."""
    (project_id,) = await new_projects(session_factory, 1)
    await seed_attribution_fixture(session_factory, project_id)
    await _promote_and_roll_up(session_factory, project_id, "a.b-c", other=False)
    export_id = await _queued_attribution_export(session_factory, project_id, key="a.b-c")

    result = await _run_export(session_factory, settings, project_id, export_id)

    assert (result["status"], result["row_count"]) == ("done", 4)
    body = store.objects[result["storage_key"]]
    assert body.decode().splitlines()[0] == ",".join(ATTRIBUTION_CSV_COLUMNS)
    rows = _rows(body)
    assert [(row["value"], row["bucket"]) for row in rows] == [
        ("y", "value"),  # t4: 0.43
        ("x", "value"),  # t1: 0.15
        ("z", "value"),  # t6: unpriced, so unknown and last among the values
        ("", "none"),  # t2, t3, t5
    ]
    assert {row["group_by"] for row in rows} == {"metadata"}
    assert {row["key"] for row in rows} == {"a.b-c"}
    assert rows[2]["cost_usd"] == ""
    assert rows[3]["traces"] == "3"
    assert rows[3]["folded_values"] == ""


async def test_rollup_other_row_is_exported_as_is(
    session_factory: SessionFactory, settings: Settings, store: StreamingMemoryStore
) -> None:
    (project_id,) = await new_projects(session_factory, 1)
    await seed_attribution_fixture(session_factory, project_id)
    await _promote_and_roll_up(session_factory, project_id, "customer", other=True)
    export_id = await _queued_attribution_export(session_factory, project_id)

    result = await _run_export(session_factory, settings, project_id, export_id)

    rows = _rows(store.objects[result["storage_key"]])
    assert [row["bucket"] for row in rows] == ["value", "value", "none", "other"]
    assert rows[-1]["value"] == ""
    assert rows[-1]["traces"] == "3"
    assert rows[-1]["cost_usd"] == "0.02000000"
    assert rows[-1]["folded_values"] == "7"
    assert {row["folded_values"] for row in rows[:-1]} == {""}


async def test_raw_export_pages_through_the_values(
    session_factory: SessionFactory,
    settings: Settings,
    store: StreamingMemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page smaller than the row count: every page is read, and the buckets are written once."""
    monkeypatch.setattr(attribution_job, "PAGE_SIZE", 1)
    (project_id,) = await new_projects(session_factory, 1)
    await seed_attribution_fixture(session_factory, project_id)
    one_day = {"from": f"{D0}T00:00:00Z", "to": f"{D1}T00:00:00Z"}
    export_id = await _queued_attribution_export(
        session_factory, project_id, group_by="user", key=None, filters=one_day
    )

    result = await _run_export(session_factory, settings, project_id, export_id)

    rows = _rows(store.objects[result["storage_key"]])
    assert [(row["value"], row["bucket"]) for row in rows] == [("u2", "value"), ("u1", "value")]
    assert result["row_count"] == 2


async def test_too_many_rows_fails_with_export_too_large(
    session_factory: SessionFactory,
    settings: Settings,
    store: StreamingMemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(attribution_job, "MAX_EXPORT_TRACES", 1)
    (project_id,) = await new_projects(session_factory, 1)
    await seed_attribution_fixture(session_factory, project_id)
    one_day = {"from": f"{D0}T00:00:00Z", "to": f"{D1}T00:00:00Z"}
    export_id = await _queued_attribution_export(
        session_factory, project_id, group_by="user", key=None, filters=one_day
    )

    result = await _run_export(session_factory, settings, project_id, export_id)

    assert (result["status"], result["error_code"]) == ("failed", "EXPORT_TOO_LARGE")
    assert store.objects == {}


async def test_key_unpromoted_after_queuing_fails_the_export_without_retry(
    session_factory: SessionFactory, settings: Settings, store: StreamingMemoryStore
) -> None:
    (project_id,) = await new_projects(session_factory, 1)
    await seed_attribution_fixture(session_factory, project_id)
    await _promote_and_roll_up(session_factory, project_id, "customer", other=False)
    export_id = await _queued_attribution_export(session_factory, project_id)
    await execute(
        session_factory,
        "UPDATE projects SET promoted_metadata_keys = '{}' WHERE id = :id",
        {"id": project_id},
    )

    result = await _run_export(session_factory, settings, project_id, export_id)

    # The fixed code, not `EXPORT_FAILED`: the failure path that never retries took it.
    assert (result["status"], result["error_code"]) == ("failed", "KEY_NOT_PROMOTED")
    assert store.objects == {}


# --- the request -------------------------------------------------------------------------------


@pytest.fixture
async def exporter(settings: Settings) -> AsyncIterator[Workspace]:
    """A workspace on an app with object storage configured (nothing connects to it)."""
    configured = settings.model_copy(
        update={
            "s3_bucket": "exports-test",
            "s3_access_key": SecretStr("test-access"),
            "s3_secret_key": SecretStr("test-secret"),
            "s3_endpoint": "http://localhost:9",
            "s3_force_path_style": True,
        }
    )
    async with _running(configured) as app, _browsers(app) as browsers:
        owner: Browser = await browsers("owner@example.com")
        yield await create_workspace(owner)


def _url(workspace: Workspace) -> str:
    return f"/api/v1/projects/{workspace.project_id}/exports"


def _window(days: int) -> dict[str, str]:
    return {"from": f"{D0}T00:00:00Z", "to": f"{D0 + timedelta(days=days)}T00:00:00Z"}


async def test_kind_defaults_to_traces_so_the_old_body_stays_valid(exporter: Workspace) -> None:
    body = {"format": "jsonl", "filters": _window(1)}
    response = await exporter.owner.post(_url(exporter), json=body)

    assert response.status_code == 202, response.text
    created = response.json()
    assert (created["kind"], created["group_by"], created["key"]) == ("traces", None, None)
    assert created["filters"]["from"] == f"{D0}T00:00:00Z"


async def test_jsonl_attribution_is_422(exporter: Workspace) -> None:
    body = {"kind": "attribution", "format": "jsonl", "group_by": "user", "filters": _window(1)}
    response = await exporter.owner.post(_url(exporter), json=body)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert [error["field"] for error in response.json()["errors"]] == ["attribution.format"]


async def test_trace_export_errors_keep_their_field_paths(exporter: Workspace) -> None:
    """The union tag of the trace body never shows: the paths are those of the old body."""
    bad_format = {"format": "xml", "filters": _window(1)}
    response = await exporter.owner.post(_url(exporter), json=bad_format)
    assert response.status_code == 422, response.text
    assert [error["field"] for error in response.json()["errors"]] == ["format"]

    bad_filter = {"kind": "traces", "format": "csv", "filters": {**_window(1), "to": "soon"}}
    response = await exporter.owner.post(_url(exporter), json=bad_filter)
    assert response.status_code == 422, response.text
    assert [error["field"] for error in response.json()["errors"]] == ["filters.to"]


async def test_trace_ids_take_1_to_200_ids_of_at_most_64_characters(
    exporter: Workspace,
) -> None:
    def body(trace_ids: list[str]) -> dict[str, Any]:
        return {"format": "csv", "filters": {**_window(1), "trace_ids": trace_ids}}

    ids = [f"{index:032x}" for index in range(200)]
    response = await exporter.owner.post(_url(exporter), json=body(ids))
    assert response.status_code == 202, response.text
    assert response.json()["filters"]["trace_ids"] == ids

    for bad in ([*ids, "f" * 32], [], ["x" * 65], [""]):
        response = await exporter.owner.post(_url(exporter), json=body(bad))
        assert response.status_code == 422, response.text
        assert response.json()["errors"][0]["field"].startswith("filters.trace_ids")


@pytest.mark.parametrize("body", [[], "x", 5])
async def test_a_body_that_is_not_an_object_is_422(exporter: Workspace, body: Any) -> None:
    response = await exporter.owner.post(_url(exporter), json=body)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_attribution_export_feature_counter(
    exporter: Workspace, session_factory: SessionFactory
) -> None:
    exports = metric_delta(FEATURE_USED, feature="attribution_export")
    body = {
        "kind": "attribution",
        "format": "csv",
        "group_by": "metadata",
        "key": "customer",
        "filters": {**_window(1), "environment": "prod"},
    }
    response = await exporter.owner.post(_url(exporter), json=body)

    assert response.status_code == 202, response.text
    created = response.json()
    assert (created["kind"], created["group_by"], created["key"]) == (
        "attribution",
        "metadata",
        "customer",
    )
    assert created["filters"] == {**_window(1), "environment": "prod"}
    assert exports.value == 1
    (stored,) = await fetch(session_factory, "SELECT kind::text AS kind, filters FROM exports", {})
    assert stored["kind"] == "attribution"
    assert stored["filters"]["group_by"] == "metadata"
    listed = await exporter.owner.get(_url(exporter))
    assert listed.json()["items"][0]["key"] == "customer"


async def test_non_promoted_key_over_24h_fails_at_create_before_queuing(
    exporter: Workspace, session_factory: SessionFactory
) -> None:
    exports = metric_delta(FEATURE_USED, feature="attribution_export")
    body = {
        "kind": "attribution",
        "format": "csv",
        "group_by": "metadata",
        "key": "customer",
        "filters": _window(2),
    }
    response = await exporter.owner.post(_url(exporter), json=body)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "KEY_NOT_PROMOTED"
    assert exports.value == 0
    rows = await fetch(session_factory, "SELECT count(*) AS n FROM exports", {})
    jobs = await fetch(session_factory, "SELECT count(*) AS n FROM jobs", {})
    assert (rows[0]["n"], jobs[0]["n"]) == (0, 0)


async def test_key_is_required_with_metadata_and_refused_otherwise(exporter: Workspace) -> None:
    missing = {
        "kind": "attribution",
        "format": "csv",
        "group_by": "metadata",
        "filters": _window(1),
    }
    response = await exporter.owner.post(_url(exporter), json=missing)
    assert response.status_code == 422, response.text
    assert response.json()["errors"] == [
        {"field": "key", "message": "required when group_by is metadata"}
    ]

    extra = {**missing, "group_by": "user", "key": "customer"}
    response = await exporter.owner.post(_url(exporter), json=extra)
    assert response.status_code == 422, response.text
    assert response.json()["errors"][0]["field"] == "key"

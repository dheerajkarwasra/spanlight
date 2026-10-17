"""Refresh a billing source now, upload a CSV of its costs, list its runs (spec §8.2, §8.4, §12).

Failure modes: a refresh hammers the provider's cost API (no throttle, or two refreshes at once
start two fetches); a refresh of a disabled source, a CSV source or a server that cannot open the
key queues a job that can only fail; a CSV upload is half accepted, echoes a cell back, keeps the
file, evaluates a formula, or passes the 1 MB API body limit only to fail on a 2 MB file; the
upload's days are not rolled up, so the report reads stale Spanlight figures; the runs list
skips or repeats a run across pages.
"""

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.body_limit import UPLOAD_PATH
from app.core.observability import FEATURE_USED
from app.db.models import AuditEvent, Job
from app.db.models.billing import BillingFetchRun, ProviderCostLine
from app.reconciliation import source_actions
from app.reconciliation.backfill import BACKFILL_JOB, backfill_payload, day_chunks
from app.reconciliation.csv_import import CSV_COLUMNS, MAX_UPLOAD_BYTES
from app.reconciliation.fetch_policy import fetch_window, parse_child_payload
from app.reconciliation.runs import child_payload
from tests.gateway_support import logged, metric_delta
from tests.helpers import Browser, create_project, create_workspace
from tests.jobs.bypass_support import fetch, seed_llm_spans
from tests.reconciliation.sources_support import (
    NEEDS_KEYS,
    TEST_ORIGIN,
    ProviderApi,
    answering,
    create_source,
    running_app,
    signed_up,
    sources_url,
)

FIXTURES = Path(__file__).parent / "fixtures"
HEADER = ",".join(CSV_COLUMNS)
# The app's clock: an upload's days must lie in the 400 days up to its UTC date.
NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)
SessionFactory = async_sessionmaker[AsyncSession]


@pytest.fixture
def provider_api() -> ProviderApi:
    return answering()


@pytest.fixture
async def app(gateway_settings: Settings, provider_api: ProviderApi) -> AsyncIterator[Any]:
    async with running_app(gateway_settings, provider_api, clock=lambda: NOW) as application:
        yield application


@pytest.fixture
async def owner(browser_factory: Any) -> Browser:
    browser: Browser = await browser_factory("owner@example.com")
    return browser


@pytest.fixture
async def org(owner: Browser) -> dict[str, str]:
    workspace = await create_workspace(owner)
    second = await create_project(owner, workspace.org_id, "Search")
    return {"org_id": workspace.org_id, "p1": workspace.project_id, "p2": second}


@pytest.fixture
async def api_source(owner: Browser, org: dict[str, str]) -> dict[str, Any]:
    # The CSV source covers the first project: a project is compared with one source per provider.
    return await create_source(owner, org["org_id"], project_ids=[org["p2"]])


@pytest.fixture
async def csv_source(owner: Browser, org: dict[str, str]) -> dict[str, Any]:
    return await create_source(
        owner,
        org["org_id"],
        kind="csv_upload",
        name="Console export",
        provider="anthropic",
        project_ids=[org["p1"]],
    )


def source_url(org_id: str, source_id: str) -> str:
    return f"{sources_url(org_id)}/{source_id}"


async def refresh(browser: Browser, org_id: str, source_id: str) -> httpx.Response:
    return await browser.post(f"{source_url(org_id, source_id)}/refresh")


async def upload(
    browser: Browser, org_id: str, source_id: str, data: bytes, name: str = "costs.csv"
) -> httpx.Response:
    return await browser.post(
        f"{source_url(org_id, source_id)}/upload", files={"file": (name, data, "text/csv")}
    )


def template() -> bytes:
    return (FIXTURES / "cost-template.csv").read_bytes()


def csv_row(**cells: str) -> str:
    values = {
        "day": "2026-10-01",
        "provider": "anthropic",
        "line_kind": "tokens",
        "amount_usd": "1.00",
        **cells,
    }
    return ",".join(values.get(column, "") for column in CSV_COLUMNS)


def csv_bytes(*rows: str) -> bytes:
    return ("\n".join((HEADER, *rows)) + "\n").encode()


async def runs_of(session_factory: SessionFactory, source_id: str) -> list[BillingFetchRun]:
    async with session_factory() as db:
        rows = await db.scalars(
            select(BillingFetchRun).where(BillingFetchRun.source_id == uuid.UUID(source_id))
        )
        return list(rows)


async def jobs(session_factory: SessionFactory) -> list[Job]:
    async with session_factory() as db:
        return list(await db.scalars(select(Job).where(Job.kind == "fetch_billing_source")))


async def lines_of(session_factory: SessionFactory, source_id: str) -> list[ProviderCostLine]:
    async with session_factory() as db:
        rows = await db.scalars(
            select(ProviderCostLine)
            .where(ProviderCostLine.source_id == uuid.UUID(source_id))
            .order_by(ProviderCostLine.day, ProviderCostLine.amount_usd)
        )
        return list(rows)


async def audit_events(session_factory: SessionFactory, action: str) -> list[AuditEvent]:
    async with session_factory() as db:
        rows = await db.scalars(select(AuditEvent).where(AuditEvent.action == action))
        return list(rows)


async def finish_runs(session_factory: SessionFactory) -> None:
    """Close every running run, as a finished fetch would."""
    async with session_factory() as db:
        await db.execute(text("UPDATE billing_fetch_runs SET status = 'ok', finished_at = now()"))
        await db.commit()


def problem(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()
    assert body["code"] == code, body
    return body


# --- refresh ---------------------------------------------------------------------------------


async def test_refresh_enqueues_and_opens_run(
    owner: Browser,
    org: dict[str, str],
    api_source: dict[str, Any],
    session_factory: SessionFactory,
) -> None:
    response = await refresh(owner, org["org_id"], api_source["id"])

    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    assert response.json() == {"run_id": run_id}
    [run] = await runs_of(session_factory, api_source["id"])
    assert str(run.id) == run_id
    assert (run.trigger, run.status, run.error) == ("manual", "running", None)
    assert str(run.requested_by) == owner.user["id"]
    # Never fetched successfully: the first fetch's month.
    day_from, day_to = fetch_window(NOW, first_fetch=True)
    assert (run.day_from, run.day_to) == (day_from, day_to)

    [job] = await jobs(session_factory)
    assert job.dedupe_key == f"fetch_billing_source:{api_source['id']}:manual:{run_id}"
    assert job.max_attempts == 3
    assert job.payload == child_payload(
        uuid.UUID(api_source["id"]),
        day_from,
        day_to,
        trigger="manual",
        requested_by=uuid.UUID(owner.user["id"]),
        run_id=run.id,
    )
    assert parse_child_payload(job.payload).run_id == run.id

    [event] = await audit_events(session_factory, "billing_source.refresh")
    assert event.target_id == api_source["id"]
    assert event.metadata_ == {
        "kind": "anthropic_admin_api",
        "provider": "anthropic",
        "project_ids": [org["p2"]],
        "run_id": run_id,
    }


async def test_refresh_rate_limit(
    owner: Browser,
    org: dict[str, str],
    api_source: dict[str, Any],
    session_factory: SessionFactory,
) -> None:
    for _ in range(2):
        assert (await refresh(owner, org["org_id"], api_source["id"])).status_code == 202
        await finish_runs(session_factory)

    third = await refresh(owner, org["org_id"], api_source["id"])

    problem(third, 429, "RATE_LIMITED")
    assert 1 <= int(third.headers["Retry-After"]) <= 300
    assert len(await runs_of(session_factory, api_source["id"])) == 2
    assert len(await jobs(session_factory)) == 2


async def test_refresh_in_progress_409(
    owner: Browser,
    org: dict[str, str],
    api_source: dict[str, Any],
    session_factory: SessionFactory,
) -> None:
    assert (await refresh(owner, org["org_id"], api_source["id"])).status_code == 202

    second = await refresh(owner, org["org_id"], api_source["id"])

    problem(second, 409, "BILLING_FETCH_IN_PROGRESS")
    assert len(await runs_of(session_factory, api_source["id"])) == 1
    assert len(await jobs(session_factory)) == 1
    assert len(await audit_events(session_factory, "billing_source.refresh")) == 1


async def test_concurrent_refreshes_create_one_run(
    owner: Browser,
    org: dict[str, str],
    api_source: dict[str, Any],
    session_factory: SessionFactory,
) -> None:
    first, second = await asyncio.gather(
        refresh(owner, org["org_id"], api_source["id"]),
        refresh(owner, org["org_id"], api_source["id"]),
    )

    # A fresh bucket holds two tokens, so the loser is refused by the run in progress, not the
    # limiter.
    accepted, refused = sorted([first, second], key=lambda response: response.status_code)
    assert accepted.status_code == 202, (first.text, second.text)
    problem(refused, 409, "BILLING_FETCH_IN_PROGRESS")
    [run] = await runs_of(session_factory, api_source["id"])
    [job] = await jobs(session_factory)
    assert job.payload["run_id"] == str(run.id)


async def test_refresh_disabled_source_409(
    owner: Browser,
    org: dict[str, str],
    api_source: dict[str, Any],
    session_factory: SessionFactory,
) -> None:
    url = source_url(org["org_id"], api_source["id"])
    assert (await owner.patch(url, json={"enabled": False})).status_code == 200

    response = await refresh(owner, org["org_id"], api_source["id"])

    problem(response, 409, "BILLING_SOURCE_DISABLED")
    assert await runs_of(session_factory, api_source["id"]) == []
    assert await jobs(session_factory) == []


async def test_refresh_when_feature_off_409_not_configured(
    gateway_settings: Settings, session_factory: SessionFactory
) -> None:
    off = gateway_settings.model_copy(update={"billing_sources_enabled": False})
    async with running_app(off, answering()) as application:
        owner = await signed_up(application, "airgapped@example.com")
        workspace = await create_workspace(owner)
        source = await create_source(owner, workspace.org_id)

        response = await refresh(owner, workspace.org_id, source["id"])

        body = problem(response, 409, "NOT_CONFIGURED")
        assert body["detail"].startswith("BILLING_SOURCES_ENABLED is off")
        await owner.http.aclose()
    assert await runs_of(session_factory, source["id"]) == []
    assert await jobs(session_factory) == []


async def test_refresh_without_credentials_keys_409_not_configured(
    owner: Browser,
    org: dict[str, str],
    api_source: dict[str, Any],
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    """The same database served by a process whose CREDENTIALS_KEYS is unset."""
    async with running_app(settings, answering()) as keyless:
        http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=keyless),
            base_url=TEST_ORIGIN,
            headers={"Origin": TEST_ORIGIN},
            cookies=owner.http.cookies,
        )
        async with http:
            response = await refresh(
                Browser(http=http, user=owner.user), org["org_id"], api_source["id"]
            )

    body = problem(response, 409, "NOT_CONFIGURED")
    assert body["detail"] == NEEDS_KEYS
    assert await runs_of(session_factory, api_source["id"]) == []


async def test_refresh_csv_kind_422(
    owner: Browser,
    org: dict[str, str],
    csv_source: dict[str, Any],
    session_factory: SessionFactory,
) -> None:
    response = await refresh(owner, org["org_id"], csv_source["id"])

    body = problem(response, 422, "VALIDATION_ERROR")
    assert [error["field"] for error in body["errors"]] == ["kind"]
    assert await runs_of(session_factory, csv_source["id"]) == []


# --- upload ----------------------------------------------------------------------------------


async def test_upload_template_creates_run_lines_and_rollups(
    owner: Browser,
    org: dict[str, str],
    csv_source: dict[str, Any],
    session_factory: SessionFactory,
) -> None:
    p1, p2 = uuid.UUID(org["p1"]), uuid.UUID(org["p2"])
    for day in (date(2026, 10, 1), date(2026, 10, 2)):
        at = datetime(day.year, day.month, day.day, 10, tzinfo=UTC)
        for project_id in (p1, p2):
            await seed_llm_spans(
                session_factory, project_id, 2, started_at=at, cost_usd=Decimal("0.5")
            )

    response = await upload(owner, org["org_id"], csv_source["id"], template())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "run_id": body["run_id"],
        "lines_upserted": 3,
        "days": ["2026-10-01", "2026-10-02"],
    }
    [run] = await runs_of(session_factory, csv_source["id"])
    assert str(run.id) == body["run_id"]
    assert (run.trigger, run.status, run.error) == ("csv_upload", "ok", None)
    assert (run.day_from, run.day_to) == (date(2026, 10, 1), date(2026, 10, 2))
    assert (run.lines_upserted, run.pages) == (3, 0)
    assert run.finished_at is not None
    assert str(run.requested_by) == owner.user["id"]

    lines = await lines_of(session_factory, csv_source["id"])
    assert [(line.day, line.amount_usd) for line in lines] == [
        (date(2026, 10, 1), Decimal("0.03")),
        (date(2026, 10, 1), Decimal("1.2345")),
        (date(2026, 10, 2), Decimal("0.75")),
    ]
    assert {line.fetch_run_id for line in lines} == {run.id}

    rollups = await fetch(
        session_factory,
        "SELECT project_id, day, priced_calls FROM cost_rollups_daily ORDER BY day",
    )
    assert [(row["project_id"], row["day"], row["priced_calls"]) for row in rollups] == [
        (p1, date(2026, 10, 1), 2),
        (p1, date(2026, 10, 2), 2),
    ]

    source = (await owner.get(source_url(org["org_id"], csv_source["id"]))).json()
    assert source["status"] == "ok"
    assert source["last_success_at"] is not None

    [event] = await audit_events(session_factory, "billing_source.upload")
    assert event.metadata_ == {
        "kind": "csv_upload",
        "provider": "anthropic",
        "project_ids": [org["p1"]],
        "run_id": body["run_id"],
        "lines": 3,
        "days": 2,
    }
    recorded = json.dumps(event.metadata_)
    assert "claude" not in recorded and "Web search" not in recorded


async def test_upload_rate_limit(
    owner: Browser,
    org: dict[str, str],
    csv_source: dict[str, Any],
    session_factory: SessionFactory,
) -> None:
    # Each upload is parsed in the threadpool and rolls up every day it covers: six an hour per
    # source, then one every ten minutes.
    data = csv_bytes(csv_row())
    for _ in range(6):
        assert (await upload(owner, org["org_id"], csv_source["id"], data)).status_code == 200

    seventh = await upload(owner, org["org_id"], csv_source["id"], data)

    problem(seventh, 429, "RATE_LIMITED")
    assert 1 <= int(seventh.headers["Retry-After"]) <= 600
    assert len(await runs_of(session_factory, csv_source["id"])) == 6


async def test_upload_replay_updates_the_same_lines(
    owner: Browser,
    org: dict[str, str],
    csv_source: dict[str, Any],
    session_factory: SessionFactory,
) -> None:
    first = await upload(owner, org["org_id"], csv_source["id"], template())
    corrected = template().replace(b"1.2345", b"1.5")

    second = await upload(owner, org["org_id"], csv_source["id"], corrected)

    assert first.status_code == second.status_code == 200, second.text
    lines = await lines_of(session_factory, csv_source["id"])
    assert len(lines) == 3
    assert Decimal("1.5") in {line.amount_usd for line in lines}
    assert {str(line.fetch_run_id) for line in lines} == {second.json()["run_id"]}


async def test_upload_over_a_long_span_queues_backfills_instead_of_rolling_up_inline(
    owner: Browser,
    org: dict[str, str],
    csv_source: dict[str, Any],
    session_factory: SessionFactory,
) -> None:
    """A January line and an October line: the request does not scan nine months of spans."""
    p1 = uuid.UUID(org["p1"])
    await seed_llm_spans(session_factory, p1, 1, started_at=datetime(2026, 10, 1, 10, tzinfo=UTC))
    data = csv_bytes(csv_row(day="2026-01-15"), csv_row(day="2026-10-01"))

    response = await upload(owner, org["org_id"], csv_source["id"], data)

    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    assert response.json()["days"] == ["2026-01-15", "2026-10-01"]
    async with session_factory() as db:
        queued = list(
            await db.scalars(select(Job).where(Job.kind == BACKFILL_JOB).order_by(Job.dedupe_key))
        )
    assert [(job.payload, job.dedupe_key, job.max_attempts) for job in queued] == [
        (
            backfill_payload(p1, date(2026, 1, 15), date(2026, 1, 15)),
            f"{BACKFILL_JOB}:{p1}:{run_id}:2026-01-15",
            3,
        ),
        (
            backfill_payload(p1, date(2026, 10, 1), date(2026, 10, 1)),
            f"{BACKFILL_JOB}:{p1}:{run_id}:2026-10-01",
            3,
        ),
    ]
    assert await fetch(session_factory, "SELECT 1 FROM cost_rollups_daily") == []


async def test_upload_within_a_month_rolls_up_inline_and_queues_nothing(
    owner: Browser, org: dict[str, str], csv_source: dict[str, Any], session_factory: SessionFactory
) -> None:
    data = csv_bytes(csv_row(day="2026-09-01"), csv_row(day="2026-10-01"))

    response = await upload(owner, org["org_id"], csv_source["id"], data)

    assert response.status_code == 200, response.text
    async with session_factory() as db:
        assert list(await db.scalars(select(Job).where(Job.kind == BACKFILL_JOB))) == []


def test_day_chunks_span_at_most_31_days() -> None:
    jan = [date(2026, 1, 1), date(2026, 1, 31), date(2026, 2, 1), date(2026, 2, 10)]

    assert day_chunks(jan) == [
        (date(2026, 1, 1), date(2026, 1, 31)),
        (date(2026, 2, 1), date(2026, 2, 10)),
    ]
    assert day_chunks([date(2026, 1, 15), date(2026, 10, 1)]) == [
        (date(2026, 1, 15), date(2026, 1, 15)),
        (date(2026, 10, 1), date(2026, 10, 1)),
    ]
    assert day_chunks([date(2026, 3, 1)]) == [(date(2026, 3, 1), date(2026, 3, 1))]


async def test_the_upload_body_limit_covers_only_the_upload_route(
    owner: Browser, org: dict[str, str], csv_source: dict[str, Any]
) -> None:
    big = {"name": "x" * (2 * 1024 * 1024)}
    url = source_url(org["org_id"], csv_source["id"])

    created = await owner.post(sources_url(org["org_id"]), json=big)
    edited = await owner.patch(url, json=big)

    for response in (created, edited):
        body = problem(response, 413, "PAYLOAD_TOO_LARGE")
        assert body["detail"] == "Request bodies are limited to 1048576 bytes."
    assert UPLOAD_PATH.fullmatch("/api/v1/orgs/o/billing-sources/s/upload") is not None
    assert UPLOAD_PATH.fullmatch("/api/v1/orgs/o/billing-sources/s/upload\n") is None
    assert UPLOAD_PATH.fullmatch("/api/v1/orgs/o/billing-sources/s/upload/x") is None


async def test_upload_errors_422_shape(
    owner: Browser,
    org: dict[str, str],
    csv_source: dict[str, Any],
    session_factory: SessionFactory,
) -> None:
    secret = "SECRET-CELL-0001"
    rows = [csv_row(amount_usd=secret, day=f"2026-09-{n:02d}") for n in range(1, 26)]

    response = await upload(owner, org["org_id"], csv_source["id"], csv_bytes(*rows))

    body = problem(response, 422, "VALIDATION_ERROR")
    errors = body["errors"]
    assert len(errors) == 20
    assert errors[0] == {
        "field": "file",
        "row": 2,
        "column": "amount_usd",
        "message": errors[0]["message"],
    }
    assert all(set(error) == {"field", "row", "column", "message"} for error in errors)
    assert [error["row"] for error in errors] == list(range(2, 22))
    assert secret not in response.text
    assert await runs_of(session_factory, csv_source["id"]) == []
    assert await lines_of(session_factory, csv_source["id"]) == []
    assert await audit_events(session_factory, "billing_source.upload") == []


async def test_upload_file_level_error_has_row_zero(
    owner: Browser, org: dict[str, str], csv_source: dict[str, Any]
) -> None:
    response = await upload(owner, org["org_id"], csv_source["id"], b"\xff\xfe not utf-8")

    body = problem(response, 422, "VALIDATION_ERROR")
    assert body["errors"] == [
        {"field": "file", "row": 0, "column": None, "message": "the file must be UTF-8 text"}
    ]


async def test_upload_too_large_413(
    owner: Browser,
    org: dict[str, str],
    csv_source: dict[str, Any],
    session_factory: SessionFactory,
) -> None:
    data = csv_bytes(csv_row())
    data += b"#" * (MAX_UPLOAD_BYTES + 1 - len(data))

    response = await upload(owner, org["org_id"], csv_source["id"], data)

    body = problem(response, 413, "PAYLOAD_TOO_LARGE")
    # The route's own check, not the 1 MB API body limit: a 2 MB file would get through.
    assert body["detail"] == "The file is larger than 5 MB."
    assert await runs_of(session_factory, csv_source["id"]) == []


async def test_upload_of_two_megabytes_passes_the_api_body_limit(
    owner: Browser,
    org: dict[str, str],
    csv_source: dict[str, Any],
    session_factory: SessionFactory,
) -> None:
    rows = [
        csv_row(day=f"2026-09-{1 + n % 28:02d}", description=f"line {n:06d} " + "x" * 60)
        for n in range(24_000)
    ]
    data = csv_bytes(*rows)
    assert 2 * 1024 * 1024 < len(data) < MAX_UPLOAD_BYTES

    response = await upload(owner, org["org_id"], csv_source["id"], data)

    assert response.status_code == 200, response.text[:500]
    assert response.json()["lines_upserted"] == 24_000


async def test_upload_not_multipart_415(
    owner: Browser,
    org: dict[str, str],
    csv_source: dict[str, Any],
    session_factory: SessionFactory,
) -> None:
    url = f"{source_url(org['org_id'], csv_source['id'])}/upload"

    as_json = await owner.post(url, json={"file": "day,provider"})
    as_text = await owner.post(url, content=template(), headers={"Content-Type": "text/csv"})

    problem(as_json, 415, "UNSUPPORTED_MEDIA_TYPE")
    problem(as_text, 415, "UNSUPPORTED_MEDIA_TYPE")
    assert await runs_of(session_factory, csv_source["id"]) == []


async def test_upload_without_a_file_field_422(
    owner: Browser, org: dict[str, str], csv_source: dict[str, Any]
) -> None:
    url = f"{source_url(org['org_id'], csv_source['id'])}/upload"

    response = await owner.post(url, files={"document": ("costs.csv", template(), "text/csv")})

    body = problem(response, 422, "VALIDATION_ERROR")
    assert [error["field"] for error in body["errors"]] == ["file"]


@pytest.mark.parametrize("day", ["0001-01-01", "9999-12-31", "2025-09-10", "2026-10-17"])
async def test_upload_day_outside_the_retention_window_422(
    owner: Browser,
    org: dict[str, str],
    csv_source: dict[str, Any],
    session_factory: SessionFactory,
    day: str,
) -> None:
    """A day older than the 400 days provider lines are kept, or after today, is refused before
    anything is stored: it would queue a backfill per month between it and the file's other days,
    and the far ends overflow the rollup's date arithmetic."""
    data = csv_bytes(csv_row(), csv_row(day=day))

    response = await upload(owner, org["org_id"], csv_source["id"], data)

    body = problem(response, 422, "VALIDATION_ERROR")
    assert [(error["row"], error["column"]) for error in body["errors"]] == [(3, "day")]
    assert await runs_of(session_factory, csv_source["id"]) == []
    assert await jobs(session_factory) == []


async def test_upload_with_two_file_parts_422(
    owner: Browser,
    org: dict[str, str],
    csv_source: dict[str, Any],
    session_factory: SessionFactory,
) -> None:
    url = f"{source_url(org['org_id'], csv_source['id'])}/upload"
    parts = [
        ("file", ("a.csv", template(), "text/csv")),
        ("file", ("b.csv", template(), "text/csv")),
    ]

    response = await owner.post(url, files=parts)

    body = problem(response, 422, "VALIDATION_ERROR")
    assert [error["field"] for error in body["errors"]] == ["file"]
    assert await runs_of(session_factory, csv_source["id"]) == []


async def test_inline_rollups_survive_a_day_at_the_end_of_the_calendar(
    org: dict[str, str], session_factory: SessionFactory, caplog: pytest.LogCaptureFixture
) -> None:
    """Defence in depth: should a day the parser refuses ever reach the inline rollup, the
    stored upload still answers 200 and the failure is logged, not raised."""
    uploaded = source_actions.Uploaded(
        run_id=uuid.uuid4(),
        lines_upserted=1,
        days=[date.max],
        project_ids=[uuid.UUID(org["p1"])],
        rollups_inline=True,
    )
    async with session_factory() as db:
        await source_actions.refresh_upload_rollups(db, uploaded)

    [event] = logged(caplog, "billing_upload_rollups_failed")
    assert event["error_type"] == "OverflowError"


async def test_upload_to_an_api_source_422(
    owner: Browser,
    org: dict[str, str],
    api_source: dict[str, Any],
    session_factory: SessionFactory,
) -> None:
    response = await upload(owner, org["org_id"], api_source["id"], template())

    body = problem(response, 422, "VALIDATION_ERROR")
    assert [error["field"] for error in body["errors"]] == ["kind"]
    assert await runs_of(session_factory, api_source["id"]) == []


async def test_upload_formula_cell_stored_verbatim(
    owner: Browser,
    org: dict[str, str],
    csv_source: dict[str, Any],
    session_factory: SessionFactory,
) -> None:
    formula = '=HYPERLINK("https://example.test/x","click")'
    quoted = '"' + formula.replace('"', '""') + '"'
    data = csv_bytes(
        csv_row(description=quoted), csv_row(day="2026-10-02", model="+cmd|' /C calc'!A0")
    )

    response = await upload(owner, org["org_id"], csv_source["id"], data)

    assert response.status_code == 200, response.text
    first, second = await lines_of(session_factory, csv_source["id"])
    assert first.description == formula
    assert second.model_raw == "+cmd|' /C calc'!A0"


# --- runs ------------------------------------------------------------------------------------


async def test_runs_keyset_pagination(
    owner: Browser,
    org: dict[str, str],
    csv_source: dict[str, Any],
    api_source: dict[str, Any],
    session_factory: SessionFactory,
) -> None:
    start = datetime(2026, 10, 10, 12, tzinfo=UTC)
    # Two runs share a start time: the id breaks the tie.
    offsets = [0, 1, 1, 2, 3]
    async with session_factory() as db:
        for offset in offsets:
            db.add(
                BillingFetchRun(
                    source_id=uuid.UUID(csv_source["id"]),
                    org_id=uuid.UUID(org["org_id"]),
                    trigger="csv_upload",
                    day_from=date(2026, 10, 1),
                    day_to=date(2026, 10, 2),
                    status="ok",
                    lines_upserted=offset,
                    requested_by=uuid.UUID(owner.user["id"]),
                    started_at=start + timedelta(minutes=offset),
                )
            )
        # Another source's run never shows.
        db.add(
            BillingFetchRun(
                source_id=uuid.UUID(api_source["id"]),
                org_id=uuid.UUID(org["org_id"]),
                trigger="manual",
                day_from=date(2026, 10, 1),
                day_to=date(2026, 10, 2),
                status="running",
                started_at=start + timedelta(minutes=10),
            )
        )
        await db.commit()
    expected = await fetch(
        session_factory,
        "SELECT id FROM billing_fetch_runs WHERE source_id = :source_id "
        "ORDER BY started_at DESC, id DESC",
        {"source_id": uuid.UUID(csv_source["id"])},
    )
    url = f"{source_url(org['org_id'], csv_source['id'])}/runs"

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, Any] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        response = await owner.get(url, params=params)
        assert response.status_code == 200, response.text
        assert response.headers["Cache-Control"] == "no-store"
        page = response.json()
        seen.extend(item["id"] for item in page["items"])
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert seen == [str(row["id"]) for row in expected]
    assert pages == 3
    first = (await owner.get(url)).json()["items"][0]
    assert first == {
        "id": first["id"],
        "trigger": "csv_upload",
        "day_from": "2026-10-01",
        "day_to": "2026-10-02",
        "status": "ok",
        "error": None,
        "lines_upserted": 3,
        "pages": 0,
        "requested_by": first["requested_by"],
        "started_at": first["started_at"],
        "finished_at": None,
    }
    assert (first["requested_by"]["id"], first["requested_by"]["email"]) == (
        owner.user["id"],
        "owner@example.com",
    )
    assert (await owner.get(url, params={"limit": 101})).status_code == 422
    assert (await owner.get(url, params={"cursor": "not-a-cursor"})).status_code == 422


async def test_routes_are_404_for_another_organization(
    owner: Browser,
    org: dict[str, str],
    api_source: dict[str, Any],
    csv_source: dict[str, Any],
    browser_factory: Any,
    session_factory: SessionFactory,
) -> None:
    stranger: Browser = await browser_factory("stranger@example.com")
    theirs = await create_workspace(stranger, org_name="B")

    for org_id in (org["org_id"], theirs.org_id):
        assert (await refresh(stranger, org_id, api_source["id"])).status_code == 404
        assert (await upload(stranger, org_id, csv_source["id"], template())).status_code == 404
        runs = await stranger.get(f"{source_url(org_id, csv_source['id'])}/runs")
        assert runs.status_code == 404
    assert await runs_of(session_factory, api_source["id"]) == []
    assert await runs_of(session_factory, csv_source["id"]) == []


async def test_feature_counters(
    owner: Browser, org: dict[str, str], api_source: dict[str, Any], csv_source: dict[str, Any]
) -> None:
    refreshes = metric_delta(FEATURE_USED, feature="billing_source_refresh")
    uploads = metric_delta(FEATURE_USED, feature="billing_csv_upload")

    assert (await refresh(owner, org["org_id"], api_source["id"])).status_code == 202
    assert (await refresh(owner, org["org_id"], api_source["id"])).status_code == 409
    assert (await upload(owner, org["org_id"], csv_source["id"], template())).status_code == 200
    bad = await upload(owner, org["org_id"], csv_source["id"], csv_bytes(csv_row(day="x")))
    assert bad.status_code == 422

    assert refreshes.value == 1  # a refused refresh is not a use
    assert uploads.value == 1  # nor is a refused file

"""The reconciliation report (spec §7.5, §8.3, §10.4, §13): one source's bill against the rollups.

Failure modes: a figure off by a rounding or a sign; a day called settled before the provider's
lag is over, or before a run fetched it after the lag; a line a later fetch no longer returns
still counted; a day past a project's retention reading as a match (a deleted batch span
explained away as untraced batch spend); a window the report cannot answer accepted; a request
that scans a year of spans to build missing rollups, or queues a backfill per request; another
organization reading the bill.

The world and its hand-computed figures are described in `report_support`.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.reconciliation.arithmetic import NO_PROVIDER_LINE_NOTE
from app.reconciliation.backfill import BACKFILL_JOB, backfill_payload
from tests.gateway_support import ManualClock
from tests.helpers import Browser, create_workspace
from tests.reconciliation.report_support import (
    D0,
    D1,
    D2,
    D3,
    INGEST_NOW,
    REPORT_NOW,
    SONNET,
    World,
    build_world,
    bypass_execute,
    bypass_fetch,
    csv_file,
    move_clock,
    pin_ingest_clock,
    provider_lines,
    read_report,
    seed,
    set_run_finished,
    upload,
)

NEXT_DAY = timedelta(days=1)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(start=INGEST_NOW)


@pytest.fixture
async def app(settings: Settings, clock: ManualClock) -> AsyncIterator[Any]:
    application = create_app(settings, clock=clock.wall)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def world(
    browser_factory: Any,
    client: httpx.AsyncClient,
    session_factory: Any,
    clock: ManualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> World:
    pin_ingest_clock(monkeypatch)
    return await build_world(browser_factory, client, session_factory, clock)


@pytest.fixture
async def seeded(world: World) -> World:
    await seed(world)
    return world


def report_of(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def day_of(report: dict[str, Any], day: date) -> dict[str, Any]:
    [found] = [entry for entry in report["days"] if entry["day"] == day.isoformat()]
    return found


def group_of(day: dict[str, Any], name: str) -> dict[str, Any]:
    [found] = [entry for entry in day["groups"] if entry["group"] == name]
    return found


def usd(value: str | None) -> Decimal | None:
    """A money field: a plain decimal string (no exponent), or null."""
    if value is None:
        return None
    assert isinstance(value, str) and "E" not in value.upper(), value
    return Decimal(value)


def explained(group: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {line["kind"]: line for line in group["explained"]}


async def test_report_figures_to_1e_8(seeded: World) -> None:
    report = report_of(await read_report(seeded))

    assert report["source"] == {
        "id": seeded.source_id,
        "provider": "anthropic",
        "kind": "csv_upload",
    }
    assert report["window"] == {"start": "2026-10-19T00:00:00Z", "end": "2026-10-23T00:00:00Z"}
    assert {project["name"] for project in report["projects"]} == {"Chatbot", "Search"}

    d1 = day_of(report, D1)
    assert (d1["provisional"], d1["incomplete"], d1["note"]) == (False, False, None)
    assert [group["group"] for group in d1["groups"]] == [
        "claude-haiku-4-5",
        "claude-opus-4-6",
        SONNET,
        "unmapped:claude-zeta-1",
    ]
    # Sonnet: provider 0.36 standard + 0.05 batch + 0.04 web search = 0.45. Spanlight: 0.15 +
    # 0.105 (fee 0.03) + 0.075 priority + 0.0375 batch = 0.3675; the cut stream has no cost.
    sonnet = group_of(d1, SONNET)
    assert usd(sonnet["provider_usd"]) == Decimal("0.45")
    assert usd(sonnet["spanlight_usd"]) == Decimal("0.3675")
    lines = explained(sonnet)
    assert list(lines) == [
        "batch_spend",
        "server_tool_fees",
        "usage_not_reported",
        "priority_commitment",
    ]
    assert usd(lines["batch_spend"]["usd"]) == Decimal("0.0125")  # 0.05 - 0.0375
    assert usd(lines["server_tool_fees"]["usd"]) == Decimal("0.01")  # 0.04 - 0.03
    assert lines["server_tool_fees"]["note"] == "4 web searches on the provider side, 3 recorded"
    assert lines["usage_not_reported"] == {
        "kind": "usage_not_reported",
        "usd": None,
        "count": 1,
        "tokens": None,
        "note": "1 call whose output tokens were never reported (1 cut stream)",
    }
    assert usd(lines["priority_commitment"]["usd"]) == Decimal("-0.075")
    assert lines["priority_commitment"]["count"] == 1
    assert usd(sonnet["explained_usd"]) == Decimal("-0.0525")
    # 0.45 - 0.3675 - (-0.0525): the priority call's standard-rate cost is not in the residual.
    assert usd(sonnet["residual_usd"]) == Decimal("0.135")
    assert sonnet["variance_ratio"] == "0.3000"
    assert sonnet["note"] is None
    assert sonnet["provider_tokens"] == {
        "uncached_input": 120_000,
        "cache_read": 0,
        "cache_write_5m": 0,
        "cache_write_1h": 0,
        "output": 4_000,
    }
    # Six calls of 20,000 input tokens; the cut stream never reported its output tokens, so the
    # output total is unknown rather than the five reported thousands.
    assert (sonnet["spanlight_tokens"]["uncached_input"], sonnet["spanlight_tokens"]["output"]) == (
        120_000,
        None,
    )

    zeta = group_of(d1, "unmapped:claude-zeta-1")
    assert (usd(zeta["provider_usd"]), zeta["spanlight_usd"]) == (Decimal("0.02"), None)
    assert explained(zeta)["unpriced_spans"]["count"] == 1
    assert explained(zeta)["unpriced_spans"]["tokens"] == 1_100
    assert (usd(zeta["residual_usd"]), zeta["variance_ratio"]) == (Decimal("0.02"), "1.0000")

    haiku = group_of(d1, "claude-haiku-4-5")
    assert (usd(haiku["provider_usd"]), usd(haiku["spanlight_usd"])) == (0, Decimal("0.015"))
    assert (usd(haiku["residual_usd"]), haiku["variance_ratio"]) == (Decimal("-0.015"), None)
    assert haiku["note"] == NO_PROVIDER_LINE_NOTE

    opus = group_of(d1, "claude-opus-4-6")
    assert usd(explained(opus)["untraced_traffic"]["usd"]) == Decimal("0.25")
    assert (usd(opus["residual_usd"]), opus["variance_ratio"]) == (0, "0.0000")

    # The day: the groups summed; the credit and the code execution are listed apart.
    assert usd(d1["provider_usd"]) == Decimal("0.72")
    assert usd(d1["spanlight_usd"]) == Decimal("0.3825")
    assert usd(d1["explained_usd"]) == Decimal("0.1975")
    assert usd(d1["residual_usd"]) == Decimal("0.14")
    assert d1["variance_ratio"] == "0.1944"
    assert (usd(d1["credits_usd"]), usd(d1["other_usd"])) == (Decimal("-1.00"), Decimal("0.02"))
    assert [(c["description"], usd(c["usd"])) for c in d1["credits"]] == [
        ("Promotional credit", Decimal("-1.00"))
    ]
    assert [(c["description"], usd(c["usd"])) for c in d1["other"]] == [
        ("Code Execution Usage", Decimal("0.02"))
    ]
    assert d1["fetched_at"] == "2026-10-26T00:00:00Z"
    assert d1["unattributed_calls"] == 0

    d2 = day_of(report, D2)
    assert (usd(d2["provider_usd"]), usd(d2["spanlight_usd"])) == (Decimal("0.15"), Decimal("0.15"))
    assert (usd(d2["residual_usd"]), d2["variance_ratio"]) == (0, "0.0000")


async def test_groups_follow_a_pattern_added_after_the_lines_were_stored(seeded: World) -> None:
    """The stored `model_pattern` of a line is a cache: an override created after the upload
    must group the line with the rollup rows relabelled under it, not split the model in two."""
    await bypass_execute(
        seeded.sessions,
        "INSERT INTO price_overrides (id, org_id, provider, model_pattern, input_per_mtok, "
        "output_per_mtok, effective_from) "
        "VALUES (:id, :org_id, 'anthropic', 'claude-zeta-1', 1, 2, :at)",
        {"id": uuid.uuid4(), "org_id": uuid.UUID(seeded.org_id), "at": REPORT_NOW},
    )
    # The rollups are rebuilt under the new pattern; the uploaded lines keep the old one.
    await bypass_execute(seeded.sessions, "DELETE FROM cost_rollups_daily", {})

    d1 = day_of(report_of(await read_report(seeded)), D1)

    assert [group["group"] for group in d1["groups"]] == [
        "claude-haiku-4-5",
        "claude-opus-4-6",
        SONNET,
        "claude-zeta-1",
    ]
    zeta = group_of(d1, "claude-zeta-1")
    assert (zeta["provider_models"], zeta["spanlight_models"]) == (
        ["claude-zeta-1"],
        ["claude-zeta-1"],
    )
    assert (usd(zeta["provider_usd"]), zeta["note"]) == (Decimal("0.02"), None)
    assert explained(zeta)["unpriced_spans"]["count"] == 1


async def test_residual_sign_when_spanlight_over(seeded: World) -> None:
    d3 = day_of(report_of(await read_report(seeded)), D3)
    sonnet = group_of(d3, SONNET)
    assert (usd(sonnet["provider_usd"]), usd(sonnet["spanlight_usd"])) == (
        Decimal("0.07"),
        Decimal("0.075"),
    )
    assert usd(sonnet["residual_usd"]) == Decimal("-0.005")
    assert sonnet["variance_ratio"] == "-0.0714"
    assert (usd(d3["residual_usd"]), d3["variance_ratio"]) == (Decimal("-0.005"), "-0.0714")


async def test_provisional_flags(world: World) -> None:
    await seed(world)
    [run] = await bypass_fetch(world.sessions, "SELECT id FROM billing_fetch_runs")

    # D3 ended at 2026-10-23 00:00: 12 hours later it is still inside the provider's lag, while
    # D1 (ended 10-21 00:00) is past it and was fetched after it.
    move_clock(world.clock, datetime(2026, 10, 23, 12, tzinfo=UTC))
    await set_run_finished(world.sessions, str(run["id"]), datetime(2026, 10, 23, 6, tzinfo=UTC))
    young = report_of(await read_report(world, D1, D3 + NEXT_DAY))
    flags = {entry["day"]: entry["provisional"] for entry in young["days"]}
    assert flags == {"2026-10-20": False, "2026-10-21": True, "2026-10-22": True}

    # The only run finished before D1's lag was over: D1 stays provisional, however late it is.
    move_clock(world.clock, REPORT_NOW)
    await set_run_finished(world.sessions, str(run["id"]), datetime(2026, 10, 22, 6, tzinfo=UTC))
    early = day_of(report_of(await read_report(world)), D1)
    assert (early["provisional"], early["fetched_at"]) == (True, "2026-10-22T06:00:00Z")

    # A later run settles it.
    await upload(world, csv_file(provider_lines()))
    settled = report_of(await read_report(world))
    assert (day_of(settled, D1)["provisional"], day_of(settled, D1)["fetched_at"]) == (
        False,
        "2026-10-26T00:00:00Z",
    )

    # D0 is covered by no run: the provider's side is unknown, never 0, and the day provisional.
    d0 = day_of(settled, D0)
    assert (d0["provisional"], d0["fetched_at"], d0["provider_usd"]) == (True, None, None)
    assert (d0["residual_usd"], d0["variance_ratio"]) == (None, None)
    assert (d0["credits_usd"], d0["other_usd"], d0["credits"], d0["other"]) == (None, None, [], [])
    assert usd(d0["spanlight_usd"]) == Decimal("0.15")
    sonnet = group_of(d0, SONNET)
    assert (sonnet["provider_usd"], sonnet["residual_usd"], sonnet["note"]) == (None, None, None)
    assert usd(sonnet["spanlight_usd"]) == Decimal("0.15")
    # Whether the provider bills priority is unknown too: the priority call is named, no USD.
    assert (sonnet["explained_usd"], d0["explained_usd"]) == (None, None)
    priority = explained(sonnet)["priority_commitment"]
    assert (priority["usd"], priority["count"]) == (None, 1)
    # Nor does its note quote a dollar figure the provider side may contradict.
    assert priority["note"] == (
        "1 priority call priced at the standard rate; commitment-billed, "
        "the provider's side is not fetched yet"
    )


async def test_beyond_retention_marks_incomplete(seeded: World) -> None:
    # One day kept: at 10-26 12:00 every span of Search before 10-25 12:00 is gone or going.
    await bypass_execute(
        seeded.sessions,
        "UPDATE projects SET retention_days = 1 WHERE id = :id",
        {"id": uuid.UUID(seeded.search.project_id)},
    )
    report = report_of(await read_report(seeded))

    assert all(entry["incomplete"] for entry in report["days"])
    d1 = day_of(report, D1)
    sonnet = explained(group_of(d1, SONNET))
    assert sonnet["beyond_retention"]["count"] == 1
    # Search's batch call may be deleted: the batch gap and the fee gap are not explained away.
    assert sonnet["batch_spend"]["usd"] is None
    assert sonnet["server_tool_fees"]["usd"] is None
    assert explained(group_of(d1, "claude-opus-4-6"))["untraced_traffic"]["usd"] is None
    assert report["summary"]["days_within_2pct"] == 0
    assert report["summary"]["max_abs_variance_ratio"] is None


async def test_lines_of_latest_ok_run_only(seeded: World) -> None:
    # A second fetch of D1 no longer returns the Opus line.
    d1_only = [line for line in provider_lines(with_opus=False) if line.startswith("2026-10-20")]
    await upload(seeded, csv_file(d1_only), finished_at=REPORT_NOW - timedelta(hours=1))

    d1 = day_of(report_of(await read_report(seeded)), D1)

    assert "claude-opus-4-6" not in {group["group"] for group in d1["groups"]}
    assert usd(d1["provider_usd"]) == Decimal("0.47")
    # The line stays in the table for audit, with the run that last wrote it.
    stored = await bypass_fetch(
        seeded.sessions, "SELECT 1 FROM provider_cost_lines WHERE model_raw = 'claude-opus-4-6'"
    )
    assert len(stored) == 1
    # D2 and D3 still read the first run's lines: the second run did not cover them.
    assert usd(day_of(report_of(await read_report(seeded)), D3)["provider_usd"]) == Decimal("0.07")


async def test_window_over_31_days_422_field_to(world: World) -> None:
    move_clock(world.clock, REPORT_NOW)
    over = await read_report(world, date(2026, 9, 1), date(2026, 10, 3))
    assert over.status_code == 422, over.text
    assert over.json()["code"] == "VALIDATION_ERROR"
    assert [error["field"] for error in over.json()["errors"]] == ["to"]

    # 31 whole days is the limit itself; a partial day widens to a whole one.
    assert (await read_report(world, date(2026, 9, 1), date(2026, 10, 2))).status_code == 200
    widened = await world.owner.get(
        world.report_url,
        params={"from": "2026-09-01T06:00:00Z", "to": "2026-10-01T18:00:00Z"},
    )
    assert report_of(widened)["window"] == {
        "start": "2026-09-01T00:00:00Z",
        "end": "2026-10-02T00:00:00Z",
    }


async def test_window_in_future_422(world: World) -> None:
    move_clock(world.clock, REPORT_NOW)
    future = await read_report(world, date(2026, 10, 20), date(2026, 10, 28))
    assert future.status_code == 422, future.text
    assert [error["field"] for error in future.json()["errors"]] == ["to"]
    # Today itself may be read (provisional).
    assert (await read_report(world, date(2026, 10, 20), date(2026, 10, 27))).status_code == 200


async def test_future_from_without_to_422_field_from(world: World) -> None:
    # `to` was not sent, so the field at fault is `from`, which asks for today or later.
    move_clock(world.clock, REPORT_NOW)
    response = await world.owner.get(world.report_url, params={"from": "2026-10-27T00:00:00Z"})
    assert response.status_code == 422, response.text
    assert [error["field"] for error in response.json()["errors"]] == ["from"]


async def test_default_window_is_the_last_14_whole_days(world: World) -> None:
    move_clock(world.clock, REPORT_NOW)
    report = report_of(await world.owner.get(world.report_url))
    assert report["window"] == {"start": "2026-10-12T00:00:00Z", "end": "2026-10-26T00:00:00Z"}


async def test_lazy_build_fills_missing_days_and_caps(seeded: World) -> None:
    before = report_of(await read_report(seeded))
    await bypass_execute(seeded.sessions, "DELETE FROM cost_rollups_daily", {})

    rebuilt = report_of(await read_report(seeded))

    assert rebuilt["days"] == before["days"]
    assert await bypass_fetch(seeded.sessions, "SELECT 1 FROM cost_rollups_daily")


async def test_lazy_build_caps_at_31_project_days_and_queues_one_backfill(world: World) -> None:
    """20 days with spans in each of two projects and no rollups: 40 project-days to build."""
    days = [date(2026, 10, 1) + offset * NEXT_DAY for offset in range(20)]
    for workspace in (world.chatbot, world.search):
        await seed_sonnet_spans(world, uuid.UUID(workspace.project_id), days)
    move_clock(world.clock, REPORT_NOW)

    report = report_of(await read_report(world, days[0], days[-1] + NEXT_DAY))

    # Chatbot (first by name) gets its 20 days, Search the 11 the cap leaves; 9 wait.
    pending = [entry for entry in report["days"] if entry["note"] == "rollup pending"]
    assert [entry["day"] for entry in pending] == [day.isoformat() for day in days[11:]]
    for entry in pending:
        assert (entry["spanlight_usd"], entry["incomplete"]) == (None, True)
        assert (entry["residual_usd"], entry["variance_ratio"]) == (None, None)
    built = [entry for entry in report["days"] if entry["note"] is None]
    assert len(built) == 11
    assert {usd(entry["spanlight_usd"]) for entry in built} == {Decimal("0.15")}

    search = uuid.UUID(world.search.project_id)
    # The request's UTC date ends the key, so a backfill that failed is queued again tomorrow.
    expected_key = f"{BACKFILL_JOB}:{search}:2026-10-12:2026-10-20:2026-10-26"
    queued = await bypass_fetch(world.sessions, "SELECT kind, dedupe_key, payload FROM jobs")
    assert queued == [
        {
            "kind": BACKFILL_JOB,
            "dedupe_key": expected_key,
            "payload": backfill_payload(search, days[11], days[-1]),
        }
    ]

    # The same window again, its rollups gone again: the same backfill, queued once.
    await bypass_execute(world.sessions, "DELETE FROM cost_rollups_daily", {})
    report_of(await read_report(world, days[0], days[-1] + NEXT_DAY))
    assert len(await bypass_fetch(world.sessions, "SELECT 1 FROM jobs")) == 1

    # The backfill failed for good; the next day the same request queues it again.
    await bypass_execute(world.sessions, "UPDATE jobs SET status = 'failed'", {})
    move_clock(world.clock, REPORT_NOW + NEXT_DAY)
    await bypass_execute(world.sessions, "DELETE FROM cost_rollups_daily", {})
    report_of(await read_report(world, days[0], days[-1] + NEXT_DAY))
    keys = await bypass_fetch(world.sessions, "SELECT dedupe_key FROM jobs ORDER BY id")
    assert [row["dedupe_key"] for row in keys] == [
        expected_key,
        f"{BACKFILL_JOB}:{search}:2026-10-12:2026-10-20:2026-10-27",
    ]


async def test_day_without_lines_or_rows_omitted(seeded: World) -> None:
    report = report_of(await read_report(seeded, date(2026, 10, 15), D3 + 3 * NEXT_DAY))
    assert [entry["day"] for entry in report["days"]] == [
        day.isoformat() for day in (D0, D1, D2, D3)
    ]


async def test_summary_counts(seeded: World) -> None:
    summary = report_of(await read_report(seeded))["summary"]
    # D0 is unfetched; of D1 (19.44%), D2 (0%) and D3 (-7.14%) only D2 is within 2%. The largest
    # group ratio is claude-zeta-1's 100%.
    assert summary == {
        "days": 4,
        "settled_days": 3,
        "days_within_2pct": 1,
        "max_abs_variance_ratio": "1.0000",
    }

    # Before D3 settles it is left out of every settled count.
    move_clock(seeded.clock, datetime(2026, 10, 24, 12, tzinfo=UTC))
    [run] = await bypass_fetch(seeded.sessions, "SELECT id FROM billing_fetch_runs")
    await set_run_finished(seeded.sessions, str(run["id"]), datetime(2026, 10, 24, 6, tzinfo=UTC))
    summary = report_of(await read_report(seeded))["summary"]
    assert summary == {
        "days": 4,
        "settled_days": 2,
        "days_within_2pct": 1,
        "max_abs_variance_ratio": "1.0000",
    }


async def test_non_member_404(seeded: World, browser_factory: Any) -> None:
    outsider: Browser = await browser_factory("outsider@example.com")
    for suffix in ("", ".csv"):
        response = await outsider.get(seeded.report_url + suffix)
        assert response.status_code == 404, response.text

    # Through the outsider's own organization, the source is not found either.
    own = await create_workspace(outsider, org_name="Other")
    url = seeded.report_url.replace(seeded.org_id, own.org_id)
    for suffix in ("", ".csv"):
        response = await outsider.get(url + suffix)
        assert response.status_code == 404, response.text


async def seed_sonnet_spans(world: World, project_id: uuid.UUID, days: list[date]) -> None:
    """One priced Sonnet span ($0.075) per day, written straight to the tables."""
    for day in days:
        trace_id = uuid.uuid4().hex
        at = datetime(day.year, day.month, day.day, 10, tzinfo=UTC)
        params = {"project_id": project_id, "trace_id": trace_id, "at": at}
        await bypass_execute(
            world.sessions,
            """
            WITH t AS (
                INSERT INTO traces (project_id, trace_id, started_at, ended_at)
                VALUES (:project_id, :trace_id, :at, :at + interval '1 second')
            )
            INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, started_at,
                               ended_at, provider, model, input_tokens, output_tokens, cost_usd)
            VALUES (:project_id, :trace_id, left(:trace_id, 16), 'llm', 'call', 'ok', :at,
                    :at + interval '1 second', 'anthropic', 'claude-sonnet-4-6', 20000, 1000,
                    0.075)
            """,
            params,
        )

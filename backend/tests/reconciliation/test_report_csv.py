"""The reconciliation report as CSV (spec §8.3, program §4 "CSV export").

Failure modes: a cell a spreadsheet runs as a formula (a model name is the organization's own
text, and an override pattern can start with `=` or `-`); a negative amount written as text, so a
spreadsheet cannot add it up; a CSV that drops or merges groups; the feature counters counting the
wrong route.
"""

import csv
import io
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.core.observability import FEATURE_USED
from app.main import create_app
from app.reconciliation.report_csv import number_cell
from tests.gateway_support import ManualClock, metric_delta
from tests.reconciliation.report_support import (
    D1,
    D3,
    INGEST_NOW,
    World,
    build_world,
    csv_file,
    csv_line,
    pin_ingest_clock,
    read_report,
    seed,
    upload,
)

CSV_HEADER = [
    "day",
    "provisional",
    "incomplete",
    "group",
    "model_pattern",
    "provider_usd",
    "spanlight_usd",
    "explained_usd",
    "residual_usd",
    "variance_ratio",
    "explained_kinds",
]


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock(start=INGEST_NOW)


@pytest.fixture
async def app(settings: Settings, clock: ManualClock) -> AsyncIterator[Any]:
    application = create_app(settings, clock=clock.wall)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def seeded(
    browser_factory: Any,
    client: httpx.AsyncClient,
    session_factory: Any,
    clock: ManualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> World:
    pin_ingest_clock(monkeypatch)
    world = await build_world(browser_factory, client, session_factory, clock)
    await seed(world)
    return world


def parsed(response: httpx.Response) -> list[list[str]]:
    assert response.status_code == 200, response.text
    return list(csv.reader(io.StringIO(response.text)))


async def test_reconciliation_csv_escapes(seeded: World) -> None:
    for pattern in ("=cmd()", "-cmd"):
        override = await seeded.owner.post(
            f"/api/v1/orgs/{seeded.org_id}/price-overrides",
            json={
                "provider": "anthropic",
                "model_pattern": pattern,
                "input_per_mtok": "1",
                "output_per_mtok": "1",
            },
        )
        assert override.status_code == 201, override.text
    lines = [csv_line(D1, "tokens", "0.01", "=cmd()"), csv_line(D1, "tokens", "0.02", "-cmd")]
    await upload(seeded, csv_file(lines))

    rows = parsed(await read_report(seeded, csv=True))

    by_group = {(row[0], row[3]): row for row in rows[1:]}
    formula = by_group[(D1.isoformat(), "'=cmd()")]
    assert formula[4] == "'=cmd()"
    # A text cell starting with `-` is escaped like any other formula prefix.
    dash = by_group[(D1.isoformat(), "'-cmd")]
    assert (dash[4], dash[5]) == ("'-cmd", "0.02000000")
    # A negative amount is a number, written plain so a spreadsheet can add it up.
    sonnet = by_group[(D3.isoformat(), "claude-sonnet-4-6")]
    assert (sonnet[8], sonnet[9]) == ("-0.00500000", "-0.0714")
    # No text cell can start a formula; only strict decimals start with `-`.
    for row in rows:
        for cell in row:
            assert cell[:1] not in ("=", "+", "@", "\t", "\r"), cell
            if cell.startswith("-"):
                assert number_cell(cell) == cell, cell


def test_number_cell_writes_only_strict_decimals_plain() -> None:
    assert [number_cell(value) for value in ("-0.5", "12", "0.00000001", "-3")] == [
        "-0.5",
        "12",
        "0.00000001",
        "-3",
    ]
    # Anything else falls back to the formula guard.
    assert [number_cell(value) for value in ("-1E-8", "+1", "-1.", "=1", "-")] == [
        "'-1E-8",
        "'+1",
        "'-1.",
        "'=1",
        "'-",
    ]


async def test_csv_header_and_row_per_group(seeded: World) -> None:
    response = await read_report(seeded, csv=True)
    report = (await read_report(seeded)).json()

    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == (
        f'attachment; filename="reconciliation-{seeded.source_id}-2026-10-19-2026-10-22.csv"'
    )
    rows = parsed(response)
    assert rows[0] == CSV_HEADER
    assert len(rows) - 1 == sum(len(day["groups"]) for day in report["days"])
    by_group = {(row[0], row[3]): row for row in rows[1:]}
    assert by_group[("2026-10-20", "claude-sonnet-4-6")] == [
        "2026-10-20",
        "false",
        "false",
        "claude-sonnet-4-6",
        "claude-sonnet-4-6",
        "0.45000000",
        "0.36750000",
        "-0.05250000",
        "0.13500000",
        "0.3000",
        "batch_spend;server_tool_fees;usage_not_reported;priority_commitment",
    ]
    # Unknown is an empty cell, never 0: D0 has no provider figure.
    d0 = by_group[("2026-10-19", "claude-sonnet-4-6")]
    assert (d0[1], d0[5], d0[6], d0[7], d0[8], d0[9]) == ("true", "", "0.15000000", "", "", "")
    zeta = by_group[("2026-10-20", "unmapped:claude-zeta-1")]
    assert (zeta[4], zeta[6]) == ("", "")


async def test_feature_counters(seeded: World) -> None:
    report = metric_delta(FEATURE_USED, feature="reconciliation_report")
    export = metric_delta(FEATURE_USED, feature="reconciliation_csv")

    assert (await read_report(seeded)).status_code == 200
    assert (report.value, export.value) == (1, 0)
    assert (await read_report(seeded, csv=True)).status_code == 200
    assert (report.value, export.value) == (1, 1)

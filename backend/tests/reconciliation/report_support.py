"""The world the reconciliation report tests read: two projects, one CSV source, three days.

Every figure is fixed so a test can compute the report by hand. Spans are ingested through
`POST /v1/traces` (priced by the real pipeline, its clock pinned to `INGEST_NOW`) and the
provider's lines are uploaded through the CSV route, which rolls the mapped projects up. The
upload's run is then dated `SETTLED_AT` (a run's finish time is the database's clock, which a test
cannot move), and the report is read at `REPORT_NOW` through the app's clock.

Prices (2026-10-16 snapshot): Claude Sonnet 4.6 $3 / $15 per million tokens, Claude Haiku 4.5
$1 / $5, the Anthropic batch factor 0.5, priority billed under a commitment (priced at the
standard rate), web search $10 per 1,000 requests.

- `D0` (2026-10-19): two Sonnet calls in Chatbot, one priority ($0.15); no provider line, no run
  covers it.
- `D1` (2026-10-20), Chatbot: two Sonnet calls ($0.15), one with 3 web searches ($0.075 + $0.03
  fee), one priority call ($0.075), one call to the unpriced `claude-zeta-1`. Search: one batch
  Sonnet call ($0.0375), one cut stream (cost unknown), one Haiku call ($0.015) the provider
  bills nowhere. Provider: Sonnet standard tokens $0.36 (100,000 input, 4,000 output), batch
  tokens $0.05 (20,000 input), 4 web searches $0.04; `claude-zeta-1` $0.02; Claude Opus 4.6
  $0.25 with no span; a $1.00 credit and $0.02 of code execution.
- `D2` (2026-10-21): two Sonnet calls in Chatbot ($0.15); the provider bills $0.15.
- `D3` (2026-10-22): one Sonnet call in Search ($0.075); the provider bills $0.07.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bypass_rls
from app.ingest import pipeline
from app.reconciliation.csv_import import CSV_COLUMNS
from tests.gateway_support import ManualClock
from tests.helpers import Browser, Workspace, create_workspace, new_trace_id, span
from tests.ingest.support import another_project, ingest_native
from tests.reconciliation.sources_support import create_source, sources_url

SessionFactory = async_sessionmaker[AsyncSession]

D0, D1, D2, D3 = date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)
INGEST_NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)
SETTLED_AT = datetime(2026, 10, 26, tzinfo=UTC)  # after D3's end + 48 h
REPORT_NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)

SONNET = "claude-sonnet-4-6"
USAGE = {"input_tokens": 20_000, "output_tokens": 1_000, "cached_tokens": 0}
HEADER = ",".join(CSV_COLUMNS)


@dataclass
class World:
    owner: Browser
    org_id: str
    chatbot: Workspace
    search: Workspace
    source_id: str
    client: httpx.AsyncClient
    sessions: SessionFactory
    clock: ManualClock

    @property
    def report_url(self) -> str:
        return f"{sources_url(self.org_id)}/{self.source_id}/reconciliation"


def pin_ingest_clock(monkeypatch: pytest.MonkeyPatch, at: datetime = INGEST_NOW) -> None:
    """Pin the ingestion pipeline's clock, so spans of the fixed days are neither old nor future."""

    class _Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return at.astimezone(tz) if tz is not None else at.replace(tzinfo=None)

    monkeypatch.setattr(pipeline, "datetime", _Clock)


def move_clock(clock: ManualClock, to: datetime) -> None:
    clock.advance((to - clock.wall()).total_seconds())


async def build_world(
    browser_factory: Any, client: httpx.AsyncClient, sessions: SessionFactory, clock: ManualClock
) -> World:
    """An organization with the projects Chatbot and Search, both mapped to one CSV source."""
    owner: Browser = await browser_factory("owner@example.com")
    chatbot = await create_workspace(owner)
    search = await another_project(chatbot, "Search")
    source = await create_source(
        owner,
        chatbot.org_id,
        kind="csv_upload",
        name="Console export",
        provider="anthropic",
        project_ids=[chatbot.project_id, search.project_id],
    )
    return World(owner, chatbot.org_id, chatbot, search, str(source["id"]), client, sessions, clock)


def call(
    day: date, model: str = SONNET, *, usage: dict[str, Any] | None = None, **fields: Any
) -> Any:
    """One Anthropic LLM span at 10:00 UTC of `day`."""
    start = datetime(day.year, day.month, day.day, 10, tzinfo=UTC)
    return span(
        trace_id=new_trace_id(),
        start=start,
        name=f"messages {model}",
        provider="anthropic",
        model=model,
        usage=dict(USAGE) if usage is None else usage,
        **fields,
    )


def chatbot_spans() -> list[Any]:
    return [
        call(D0),
        call(D0, usage={**USAGE, "service_tier": "priority"}),
        call(D1),
        call(D1),
        call(D1, usage={**USAGE, "server_tool_use": {"web_search_requests": 3}}),
        call(D1, usage={**USAGE, "service_tier": "priority"}),
        call(D1, "claude-zeta-1", usage={"input_tokens": 1_000, "output_tokens": 100}),
        call(D2),
        call(D2),
    ]


def search_spans() -> list[Any]:
    return [
        call(D1, usage={**USAGE, "service_tier": "batch"}),
        call(
            D1,
            usage={"input_tokens": 20_000, "cached_tokens": 0},
            attributes={"usage.unreported_reason": "client_disconnected"},
        ),
        call(D1, "claude-haiku-4-5", usage={"input_tokens": 10_000, "output_tokens": 1_000}),
        call(D3),
    ]


def csv_line(day: date, kind: str, amount: str, model: str = "", **cells: str) -> str:
    values = {
        "day": day.isoformat(),
        "provider": "anthropic",
        "line_kind": kind,
        "amount_usd": amount,
        "model": model,
        "token_type": "",
        "service_tier": "",
        "quantity": "",
        "description": "",
        "provider_scope": "",
        **cells,
    }
    return ",".join(values[column] for column in CSV_COLUMNS)


def provider_lines(*, with_opus: bool = True) -> list[str]:
    tokens = {"token_type": "uncached_input", "service_tier": "standard"}
    output = {"token_type": "output", "service_tier": "standard"}
    batch = {"token_type": "uncached_input", "service_tier": "batch"}
    rows = [
        csv_line(D1, "tokens", "0.30", SONNET, quantity="100000", **tokens),
        csv_line(D1, "tokens", "0.06", SONNET, quantity="4000", **output),
        csv_line(D1, "tokens", "0.05", SONNET, quantity="20000", **batch),
        csv_line(D1, "web_search", "0.04", SONNET, quantity="4", description="Web search"),
        csv_line(D1, "tokens", "0.02", "claude-zeta-1", **tokens),
        csv_line(D1, "credit", "-1.00", description="Promotional credit"),
        csv_line(D1, "code_execution", "0.02", description="Code Execution Usage"),
        csv_line(D2, "tokens", "0.10", SONNET, **tokens),
        csv_line(D2, "tokens", "0.05", SONNET, **output),
        csv_line(D3, "tokens", "0.07", SONNET, **tokens),
    ]
    if with_opus:
        rows.insert(5, csv_line(D1, "tokens", "0.25", "claude-opus-4-6", **output))
    return rows


def csv_file(lines: list[str]) -> bytes:
    return ("\r\n".join([HEADER, *lines]) + "\r\n").encode()


async def upload(world: World, data: bytes, *, finished_at: datetime = SETTLED_AT) -> str:
    """Upload `data` to the source and date its run's finish `finished_at`; returns the run id."""
    response = await world.owner.post(
        f"{sources_url(world.org_id)}/{world.source_id}/upload",
        files={"file": ("costs.csv", data, "text/csv")},
    )
    assert response.status_code == 200, response.text
    run_id = str(response.json()["run_id"])
    await set_run_finished(world.sessions, run_id, finished_at)
    return run_id


async def set_run_finished(sessions: SessionFactory, run_id: str, at: datetime) -> None:
    async with sessions() as db:
        await db.execute(
            text("UPDATE billing_fetch_runs SET finished_at = :at WHERE id = :id"),
            {"at": at, "id": uuid.UUID(run_id)},
        )
        await db.commit()


async def seed(world: World) -> None:
    """Ingest every span of the module docstring, then upload the provider's lines."""
    for workspace, spans in ((world.chatbot, chatbot_spans()), (world.search, search_spans())):
        response = await ingest_native(world.client, workspace, spans)
        assert response.json() == {"accepted": len(spans), "rejected": []}, response.text
    await upload(world, csv_file(provider_lines()))
    move_clock(world.clock, REPORT_NOW)


async def read_report(
    world: World, start: date = D0, end: date = D3 + timedelta(days=1), *, csv: bool = False
) -> httpx.Response:
    url = world.report_url + (".csv" if csv else "")
    params = {"from": f"{start.isoformat()}T00:00:00Z", "to": f"{end.isoformat()}T00:00:00Z"}
    return await world.owner.get(url, params=params)


async def bypass_execute(sessions: SessionFactory, sql: str, params: dict[str, Any]) -> None:
    async with sessions() as db:
        await bypass_rls(db)
        await db.execute(text(sql), params)
        await db.commit()


async def bypass_fetch(
    sessions: SessionFactory, sql: str, params: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    async with sessions() as db:
        await bypass_rls(db)
        return [dict(row) for row in (await db.execute(text(sql), params or {})).mappings()]

"""Provider cost-report fetchers over the vetted client (spec §4.1-4.3, §10.2 steps 3-4, 6).

Failure modes: a second page is never asked for, so a month of lines silently goes missing; the
usage report is not joined, so token lines carry no counts; a 401 is retried forever, or a 429
ignores the provider's `Retry-After`; a hung provider holds the worker past the page timeout; a
provider that always says `has_more` keeps the worker paging; a failure log carries the key.
"""

import importlib
import io
import json
import logging
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.config import Settings
from app.core.logging import configure_logging
from app.gateway.http import build_http_client
from app.reconciliation.fetchers import FetchFailed, fetcher_for, probe
from app.reconciliation.fetchers import base as fetch_base
from app.reconciliation.types import MappedLine
from app.reconciliation.usage_merge import PRIORITY_DESCRIPTION
from tests.gateway_support import public_resolver
from tests.reconciliation.fetch_support import (
    ANTHROPIC_COST_PATH,
    ANTHROPIC_KEY,
    ANTHROPIC_USAGE_PATH,
    DAY_FROM,
    DAY_TO,
    OPENAI_COST_PATH,
    OPENAI_KEY,
    OPENAI_USAGE_PATH,
    PATTERNS,
    Reply,
    ScriptedApi,
    fixture_page,
    page,
    single_page,
    split_pages,
    vetted_client,
)


def anthropic_api() -> ScriptedApi:
    return ScriptedApi(
        routes={
            ANTHROPIC_COST_PATH: split_pages("anthropic_cost_page.json"),
            ANTHROPIC_USAGE_PATH: single_page("anthropic_usage_page.json"),
        }
    )


def openai_api() -> ScriptedApi:
    return ScriptedApi(
        routes={
            OPENAI_COST_PATH: split_pages("openai_costs_page.json"),
            OPENAI_USAGE_PATH: single_page("openai_usage_page.json"),
        }
    )


async def fetch_anthropic(
    settings: Settings, api: ScriptedApi, scope: str | None = "default"
) -> list[MappedLine]:
    async with vetted_client(settings, api) as http:
        result = await fetcher_for("anthropic_admin_api").fetch(
            http, ANTHROPIC_KEY, DAY_FROM, DAY_TO, scope, PATTERNS
        )
    return result.lines


async def failure_of(settings: Settings, reply: Reply) -> FetchFailed:
    api = ScriptedApi(default=reply)
    with pytest.raises(FetchFailed) as raised:
        await fetch_anthropic(settings, api)
    return raised.value


def find(lines: list[MappedLine], day: date, description: str) -> MappedLine:
    matching = [line for line in lines if line.day == day and line.description == description]
    assert len(matching) == 1, matching
    return matching[0]


async def test_anthropic_pagination_follows_next_page(settings: Settings) -> None:
    api = anthropic_api()
    async with vetted_client(settings, api) as http:
        result = await fetcher_for("anthropic_admin_api").fetch(
            http, ANTHROPIC_KEY, DAY_FROM, DAY_TO, "default", PATTERNS
        )

    # Two cost-report pages, then the one usage-report page.
    assert result.pages == 3
    assert api.paths() == [ANTHROPIC_COST_PATH, ANTHROPIC_COST_PATH, ANTHROPIC_USAGE_PATH]
    assert "page" not in api.requests[0].url.params
    assert api.requests[1].url.params["page"] == "page_two"
    days = {line.day for line in result.lines}
    assert days == {date(2026, 10, 1), date(2026, 10, 2)}
    first = find(result.lines, date(2026, 10, 1), "Claude Sonnet 4.5 Usage - Input Tokens")
    assert first.amount_usd == Decimal("1.23450000")
    second = find(result.lines, date(2026, 10, 2), "Claude Sonnet 4.5 Usage - Output Tokens")
    assert second.amount_usd == Decimal("2.00000000")
    # The other workspace's line is outside the `default` scope.
    assert all(line.provider_scope is None for line in result.lines)


async def test_anthropic_merges_usage_quantities(settings: Settings) -> None:
    lines = await fetch_anthropic(settings, anthropic_api())

    day = date(2026, 10, 1)
    assert find(lines, day, "Claude Sonnet 4.5 Usage - Input Tokens").quantity == 41150
    assert find(lines, day, "Claude Sonnet 4.5 Usage - Output Tokens").quantity == 16667
    assert find(lines, day, "Claude Sonnet 4.5 Usage - Cache Read Input Tokens").quantity == 21667
    assert find(lines, day, "Web Search Usage").quantity is None
    priority = find(lines, day, PRIORITY_DESCRIPTION)
    assert priority.service_tier == "priority"
    assert priority.quantity == 5000 + 1200 + 1500
    assert priority.amount_usd == Decimal(0)
    assert priority.model_pattern == "claude-opus-4-1"
    later = date(2026, 10, 2)
    assert find(lines, later, "Claude Sonnet 4.5 Usage - Input Tokens").quantity == 33333


async def test_openai_pagination(settings: Settings) -> None:
    api = openai_api()
    async with vetted_client(settings, api) as http:
        result = await fetcher_for("openai_costs_api").fetch(
            http, OPENAI_KEY, DAY_FROM, DAY_TO, "proj_abc123", PATTERNS
        )

    assert result.pages == 3
    assert api.paths() == [OPENAI_COST_PATH, OPENAI_COST_PATH, OPENAI_USAGE_PATH]
    assert api.requests[1].url.params["page"] == "page_two"
    assert {line.day for line in result.lines} == {date(2026, 10, 1), date(2026, 10, 2)}
    assert all(line.provider_scope == "proj_abc123" for line in result.lines)
    first_day = [line for line in result.lines if line.day == date(2026, 10, 1)]
    inputs = [
        line
        for line in first_day
        if line.description == "gpt-4o-2024-08-06, input" and line.line_kind == "tokens"
    ]
    assert [line.amount_usd for line in inputs] == [Decimal("0.10000000")]
    # 45,000 input tokens of which 5,000 cached: 40,000 uncached.
    assert inputs[0].quantity == 40000
    later = [line for line in result.lines if line.day == date(2026, 10, 2)]
    assert {line.amount_usd for line in later} == {Decimal("0.30000000"), Decimal("0.60000000")}


async def test_openai_amounts_parse_without_a_float(settings: Settings) -> None:
    exact = {
        "start_time": 1790812800,
        "end_time": 1790899200,
        "results": [
            {
                "amount": {"value": 0.1, "currency": "usd"},
                "line_item": "gpt-4o, output",
                "project_id": None,
            }
        ],
    }
    body = (
        b'{"data": [{"start_time": 1790812800, "end_time": 1790899200, "results": [{"amount": '
        b'{"value": 0.123456785, "currency": "usd"}, "line_item": "gpt-4o, output", '
        b'"project_id": null}]}], "has_more": false, "next_page": null}'
    )
    api = ScriptedApi(
        routes={
            OPENAI_COST_PATH: [Reply(body=body)],
            OPENAI_USAGE_PATH: [Reply(json=page([exact | {"results": []}]))],
        }
    )
    async with vetted_client(settings, api) as http:
        result = await fetcher_for("openai_costs_api").fetch(
            http, OPENAI_KEY, DAY_FROM, DAY_TO, None, PATTERNS
        )

    # Half-up at the eighth decimal of the provider's text; a float would round down.
    assert [line.amount_usd for line in result.lines] == [Decimal("0.12345679")]


async def test_query_strings_match_the_spec(settings: Settings) -> None:
    anthropic = anthropic_api()
    await fetch_anthropic(settings, anthropic)
    cost, usage = anthropic.requests[0].url.params, anthropic.requests[2].url.params
    assert cost["starting_at"] == "2026-10-01T00:00:00Z"
    assert cost["ending_at"] == "2026-10-03T00:00:00Z"
    assert cost["bucket_width"] == "1d"
    assert cost["limit"] == "31"
    assert cost.get_list("group_by[]") == ["description", "workspace_id"]
    assert usage["bucket_width"] == "1d"
    assert usage["limit"] == "31"
    assert usage.get_list("group_by[]") == [
        "model",
        "service_tier",
        "context_window",
        "workspace_id",
    ]

    openai = openai_api()
    async with vetted_client(settings, openai) as http:
        await fetcher_for("openai_costs_api").fetch(http, OPENAI_KEY, DAY_FROM, DAY_TO, None, [])
    costs, completions = openai.requests[0].url.params, openai.requests[2].url.params
    assert costs["start_time"] == "1790812800"
    assert costs["end_time"] == str(1790812800 + 2 * 86400)
    assert costs["bucket_width"] == "1d"
    assert costs["limit"] == "31"
    assert costs.get_list("group_by[]") == ["line_item", "project_id"]
    assert completions["bucket_width"] == "1d"
    assert completions.get_list("group_by[]") == ["model", "batch", "project_id"]


async def test_headers_carry_key_version_and_user_agent(settings: Settings) -> None:
    anthropic = anthropic_api()
    await fetch_anthropic(settings, anthropic)
    for request in anthropic.requests:
        assert request.headers["x-api-key"] == ANTHROPIC_KEY
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in request.headers
        assert request.headers["user-agent"] == fetch_base.USER_AGENT
    assert fetch_base.USER_AGENT.startswith("spanlight/")
    assert fetch_base.USER_AGENT.endswith(" (https://spanlight.dev)")

    openai = openai_api()
    async with vetted_client(settings, openai) as http:
        await fetcher_for("openai_costs_api").fetch(http, OPENAI_KEY, DAY_FROM, DAY_TO, None, [])
    for request in openai.requests:
        assert request.headers["authorization"] == f"Bearer {OPENAI_KEY}"
        assert "x-api-key" not in request.headers
        assert request.headers["user-agent"] == fetch_base.USER_AGENT


@pytest.mark.parametrize(("status", "line"), [(401, "401 Unauthorized"), (403, "403 Forbidden")])
async def test_401_raises_auth_failed_no_retry(settings: Settings, status: int, line: str) -> None:
    failure = await failure_of(settings, Reply(status=status, json={"error": "nope"}))
    assert failure.status_line == line
    assert failure.retry == "none"
    assert failure.retry_after_s is None


@pytest.mark.parametrize(
    ("retry_after", "expected"),
    [("120", 120.0), ("0.5", 0.5), ("soon", None), (None, None)],
)
async def test_429_raises_rate_limited_with_retry_after(
    settings: Settings, retry_after: str | None, expected: float | None
) -> None:
    headers = {} if retry_after is None else {"retry-after": retry_after}
    failure = await failure_of(settings, Reply(status=429, json={}, headers=headers))
    assert failure.status_line == "429 Too Many Requests"
    assert failure.retry == "rate_limited"
    assert failure.retry_after_s == expected


async def test_429_retry_after_as_http_date(settings: Settings) -> None:
    headers = {"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"}
    failure = await failure_of(settings, Reply(status=429, json={}, headers=headers))
    # A date in the past means "now", never a negative delay.
    assert failure.retry_after_s == 0.0


@pytest.mark.parametrize(
    ("status", "line"),
    [(500, "500 Internal Server Error"), (503, "503 Service Unavailable"), (529, "529")],
)
async def test_5xx_raises_transient(settings: Settings, status: int, line: str) -> None:
    failure = await failure_of(settings, Reply(status=status, json={}))
    assert failure.status_line == line
    assert failure.retry == "transient"


async def test_other_4xx_is_not_retried(settings: Settings) -> None:
    failure = await failure_of(settings, Reply(status=400, json={}))
    assert failure.status_line == "400 Bad Request"
    assert failure.retry == "none"


async def test_timeout_raises_transient_timeout(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch_base, "PAGE_TIMEOUT_S", 0.05)
    failure = await failure_of(settings, Reply(json=page([]), delay_s=2.0))
    assert failure.status_line == "timeout"
    assert failure.retry == "transient"


async def test_too_many_pages(settings: Settings) -> None:
    endless = Reply(json=page([], has_more=True, next_page="again"))
    api = ScriptedApi(default=endless)
    with pytest.raises(FetchFailed) as raised:
        await fetch_anthropic(settings, api)
    assert raised.value.status_line == "too many pages"
    assert raised.value.retry == "none"
    assert len(api.requests) == fetch_base.MAX_PAGES == 40


async def test_has_more_without_next_page_is_invalid(settings: Settings) -> None:
    failure = await failure_of(settings, Reply(json=page([], has_more=True)))
    assert failure.status_line == "invalid response"


async def test_probe_success_and_failure(settings: Settings) -> None:
    ok = ScriptedApi(default=Reply(json=page([])))
    async with vetted_client(settings, ok) as http:
        assert await probe(http, "anthropic_admin_api", ANTHROPIC_KEY, today=DAY_TO) is None
        assert await probe(http, "openai_costs_api", OPENAI_KEY, today=DAY_TO) is None
    anthropic_query, openai_query = ok.requests[0].url.params, ok.requests[1].url.params
    assert ok.paths() == [ANTHROPIC_COST_PATH, OPENAI_COST_PATH]
    assert anthropic_query["starting_at"] == "2026-10-01T00:00:00Z"
    assert anthropic_query["ending_at"] == "2026-10-02T00:00:00Z"
    assert anthropic_query["limit"] == "1"
    assert openai_query["start_time"] == "1790812800"
    assert openai_query["end_time"] == "1790899200"
    assert openai_query["limit"] == "1"

    denied = ScriptedApi(default=Reply(status=401, json={}))
    async with vetted_client(settings, denied) as http:
        assert await probe(http, "anthropic_admin_api", ANTHROPIC_KEY) == "401 Unauthorized"
        assert await probe(http, "openai_costs_api", OPENAI_KEY) == "401 Unauthorized"
    garbled = ScriptedApi(default=Reply(body=b"<html>"))
    async with vetted_client(settings, garbled) as http:
        assert await probe(http, "anthropic_admin_api", ANTHROPIC_KEY) == "invalid response"


async def test_probe_times_out_after_its_own_limit(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The package exports the `probe` function under the module's name.
    probe_module = importlib.import_module("app.reconciliation.fetchers.probe")
    monkeypatch.setattr(probe_module, "PROBE_TIMEOUT_S", 0.05)
    slow = ScriptedApi(default=Reply(json=page([]), delay_s=2.0))
    async with vetted_client(settings, slow) as http:
        assert await probe(http, "openai_costs_api", OPENAI_KEY) == "timeout"


@pytest.fixture
def app_logs(caplog: pytest.LogCaptureFixture) -> Iterator[io.StringIO]:
    """Logging routed as in production (structlog through stdlib), at DEBUG, into a buffer.

    `caplog` keeps receiving records too; the root logger is restored afterwards.
    """
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    buffer = io.StringIO()
    configure_logging("DEBUG", json=True, stream=buffer)
    root.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG)
    yield buffer
    root.handlers = saved_handlers
    root.setLevel(saved_level)


@pytest.mark.parametrize(
    "reply",
    [
        Reply(status=401, body=f'{{"error": "bad key {ANTHROPIC_KEY}"}}'.encode()),
        Reply(body=f'{{"data": "{ANTHROPIC_KEY}"}}'.encode()),
        Reply(body=b"{"),
    ],
    ids=["401-echoing-key", "invalid-echoing-key", "truncated"],
)
async def test_fetchers_log_no_key(
    settings: Settings,
    caplog: pytest.LogCaptureFixture,
    app_logs: io.StringIO,
    reply: Reply,
) -> None:
    failure = await failure_of(settings, reply)

    secret = ANTHROPIC_KEY.removeprefix("sk-ant-admin01-")
    windows = {secret[start : start + 8] for start in range(len(secret) - 7)}
    captured = app_logs.getvalue() + caplog.text
    captured += "".join(repr(record.msg) + repr(record.args) for record in caplog.records)
    captured += str(failure) + repr(failure) + repr(failure.args)
    assert not [window for window in windows if window in captured]
    assert "billing_fetch_failed" in app_logs.getvalue(), "a failed fetch logs why"


async def test_key_with_a_control_character_fails_without_retry(
    settings: Settings, caplog: pytest.LogCaptureFixture, app_logs: io.StringIO
) -> None:
    # MockTransport skips h11, which refuses such a header value on the wire; mimic it.
    key = "sk-ant-admin01-Q7xv93Lm\nZp4RtKc8WnBy2HdJfE6gUaS1"
    seen: list[httpx.Request] = []

    def h11_like(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        raise httpx.LocalProtocolError(f"Illegal header value {request.headers['x-api-key']!r}")

    http = build_http_client(
        settings, transport=httpx.MockTransport(h11_like), resolver=public_resolver
    )
    async with http:
        with pytest.raises(FetchFailed) as raised:
            await fetcher_for("anthropic_admin_api").fetch(
                http, key, DAY_FROM, DAY_TO, None, PATTERNS
            )
        assert await probe(http, "anthropic_admin_api", key) == "key cannot be decrypted"

    assert raised.value.status_line == "key cannot be decrypted"
    assert raised.value.retry == "none"
    assert len(seen) == 2  # one page each for the fetch and the probe; nothing retried
    captured = app_logs.getvalue() + caplog.text + repr(raised.value.args)
    for part in key.split("\n"):
        assert part.removeprefix("sk-ant-admin01-") not in captured


async def test_redirects_are_not_followed_even_by_a_permissive_client() -> None:
    api = ScriptedApi(
        default=Reply(status=302, json={}, headers={"location": "https://api.anthropic.com/x"})
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(api), follow_redirects=True) as http:
        with pytest.raises(FetchFailed) as raised:
            await fetcher_for("anthropic_admin_api").fetch(
                http, ANTHROPIC_KEY, DAY_FROM, DAY_TO, None, PATTERNS
            )
    assert raised.value.status_line == "302 Found"
    assert len(api.requests) == 1


async def test_probe_timeout_is_logged(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, app_logs: io.StringIO
) -> None:
    probe_module = importlib.import_module("app.reconciliation.fetchers.probe")
    monkeypatch.setattr(probe_module, "PROBE_TIMEOUT_S", 0.05)
    slow = ScriptedApi(default=Reply(json=page([]), delay_s=2.0))
    async with vetted_client(settings, slow) as http:
        assert await probe(http, "anthropic_admin_api", ANTHROPIC_KEY) == "timeout"
    failed = [
        json.loads(line)
        for line in app_logs.getvalue().splitlines()
        if '"billing_fetch_failed"' in line
    ]
    assert [entry["status_line"] for entry in failed] == ["timeout"]


async def test_buckets_outside_the_requested_window_are_dropped(settings: Settings) -> None:
    """A stray bucket would re-point an older line's run to one that does not cover its day,
    and the report, which reads each day's lines from the run covering it, would lose the line."""
    costs = fixture_page("anthropic_cost_page.json")["data"]
    usage = fixture_page("anthropic_usage_page.json")["data"]
    stray_cost = costs[0] | {
        "starting_at": "2026-09-30T00:00:00Z",
        "ending_at": "2026-10-01T00:00:00Z",
    }
    stray_usage = usage[0] | {
        "starting_at": "2026-10-03T00:00:00Z",
        "ending_at": "2026-10-04T00:00:00Z",
    }
    anthropic = ScriptedApi(
        routes={
            ANTHROPIC_COST_PATH: [Reply(json=page([stray_cost, *costs]))],
            ANTHROPIC_USAGE_PATH: [Reply(json=page([*usage, stray_usage]))],
        }
    )
    openai_costs = fixture_page("openai_costs_page.json")["data"]
    stray = openai_costs[0] | {"start_time": 1790812800 - 86400, "end_time": 1790812800}
    openai = ScriptedApi(
        routes={
            OPENAI_COST_PATH: [Reply(json=page([stray, *openai_costs]))],
            OPENAI_USAGE_PATH: single_page("openai_usage_page.json"),
        }
    )

    async with vetted_client(settings, anthropic) as http:
        claude = await fetcher_for("anthropic_admin_api").fetch(
            http, ANTHROPIC_KEY, DAY_FROM, DAY_TO, None, PATTERNS
        )
    async with vetted_client(settings, openai) as http:
        gpt = await fetcher_for("openai_costs_api").fetch(
            http, OPENAI_KEY, DAY_FROM, DAY_TO, None, PATTERNS
        )

    for result in (claude, gpt):
        assert {line.day for line in result.lines} == {DAY_FROM, DAY_TO}

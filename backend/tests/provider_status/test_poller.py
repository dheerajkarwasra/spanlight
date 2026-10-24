"""The status poller fetches each fixed feed, upserts what it parses and records how the poll went.

Every test runs the real poller and the real status client over an `httpx.MockTransport`, against
the real tables. The feeds are the recorded fixtures of the adapter tests.
"""

import json
from collections.abc import Callable
from datetime import datetime, timedelta

import httpx
import pytest

from app.provider_status.adapters import FeedAdapter
from app.provider_status.adapters.anthropic import anthropic
from app.provider_status.adapters.fields import MAX_INCIDENTS_PER_FEED
from app.provider_status.adapters.openai import openai
from app.provider_status.poller import MAX_FEED_BYTES, PollOutcome, PollResult, poll_one
from tests.provider_status.poll_support import (
    ANTHROPIC_FEED,
    NOW,
    OPENAI_FEED,
    OPENAI_RSS,
    FeedServer,
    Reply,
    SessionFactory,
    anthropic_feed,
    chunks,
    first_anthropic_incident,
    fixture,
    incidents,
    source,
)

LATER = NOW + timedelta(minutes=5)


async def _poll(
    session_factory: SessionFactory,
    server: FeedServer,
    adapter: FeedAdapter,
    *,
    now: datetime = NOW,
) -> PollResult:
    async with server.client() as client, session_factory() as db:
        return await poll_one(client, db, adapter, now=now)


def _not_modified_when_matching(etag: str) -> Callable[[httpx.Request], httpx.Response]:
    def reply(request: httpx.Request) -> httpx.Response:
        if request.headers.get("if-none-match") == etag:
            return httpx.Response(304)
        return httpx.Response(200, content=fixture(ANTHROPIC_FEED), headers={"etag": etag})

    return reply


async def test_200_upserts_and_records_fetched_with_etag(session_factory: SessionFactory) -> None:
    server = FeedServer()
    server.answer(
        anthropic.url,
        httpx.Response(
            200,
            content=fixture(ANTHROPIC_FEED),
            headers={"etag": 'W/"v1"', "last-modified": "Thu, 16 Oct 2026 11:00:00 GMT"},
        ),
    )

    result = await _poll(session_factory, server, anthropic)

    assert result.outcome == PollOutcome.FETCHED
    assert result.upserted == 10
    stored = await incidents(session_factory, "anthropic")
    assert len(stored) == 10
    assert {row["source"] for row in stored} == {"statuspage_json"}
    assert {row["fetched_at"] for row in stored} == {NOW}
    feed = await source(session_factory, "anthropic")
    assert feed["etag"] == 'W/"v1"'
    assert feed["last_modified"] == "Thu, 16 Oct 2026 11:00:00 GMT"
    assert feed["last_outcome"] == "fetched"
    assert feed["last_polled_at"] == feed["last_success_at"] == NOW
    assert feed["consecutive_failures"] == 0
    assert feed["last_error_type"] is None
    # The poller fetched the adapter's constant URL, uncompressed, without following anything.
    [request] = server.requests
    assert str(request.url) == anthropic.url
    assert request.headers["accept-encoding"] == "identity"
    assert "if-none-match" not in request.headers


async def test_304_writes_only_last_polled_at(session_factory: SessionFactory) -> None:
    """A 304 leaves the incidents and the validators alone; only the poll state moves.

    The poll state includes `last_success_at` and the failure count: a 304 is the feed answering
    "nothing changed", so it is a success, or a quiet feed would look stale after 30 minutes.
    """
    server = FeedServer()
    server.answer(anthropic.url, _not_modified_when_matching('W/"v1"'))
    await _poll(session_factory, server, anthropic)

    result = await _poll(session_factory, server, anthropic, now=LATER)

    assert result.outcome == PollOutcome.NOT_MODIFIED
    assert server.requests[-1].headers["if-none-match"] == 'W/"v1"'
    assert {row["fetched_at"] for row in await incidents(session_factory, "anthropic")} == {NOW}
    feed = await source(session_factory, "anthropic")
    assert feed["etag"] == 'W/"v1"'
    assert feed["last_outcome"] == "not_modified"
    assert feed["last_polled_at"] == feed["last_success_at"] == LATER
    assert feed["consecutive_failures"] == 0


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


def _refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


@pytest.mark.parametrize(
    ("reply", "outcome", "error_type"),
    [
        (httpx.Response(500, content=b"oops"), PollOutcome.HTTP_ERROR, "HTTPStatusError"),
        (
            httpx.Response(301, headers={"location": "https://evil.example/"}),
            PollOutcome.HTTP_ERROR,
            "HTTPStatusError",
        ),
        (_refused, PollOutcome.HTTP_ERROR, "ConnectError"),
        (_timeout, PollOutcome.TIMEOUT, "ReadTimeout"),
        (
            httpx.Response(200, content=b"x" * (3 * 1024 * 1024)),
            PollOutcome.TOO_LARGE,
            "FeedTooLarge",
        ),
        (
            lambda _: httpx.Response(200, content=chunks(MAX_FEED_BYTES + 1)),
            PollOutcome.TOO_LARGE,
            "FeedTooLarge",
        ),
        (httpx.Response(200, content=b"<html>"), PollOutcome.PARSE_ERROR, "FeedParseError"),
    ],
    ids=["500", "redirect", "connect", "timeout", "3mb", "3mb-chunked", "not-json"],
)
async def test_500_timeout_and_3mb_body_record_their_outcomes_and_count_failures(
    session_factory: SessionFactory, reply: Reply, outcome: PollOutcome, error_type: str
) -> None:
    server = FeedServer()
    server.answer(anthropic.url, httpx.Response(200, content=fixture(ANTHROPIC_FEED)))
    await _poll(session_factory, server, anthropic)
    server.answer(anthropic.url, reply)

    first = await _poll(session_factory, server, anthropic, now=LATER)
    await _poll(session_factory, server, anthropic, now=LATER + timedelta(minutes=5))

    assert first.outcome == outcome
    assert first.error_type == error_type
    feed = await source(session_factory, "anthropic")
    assert feed["last_outcome"] == outcome.value
    assert feed["consecutive_failures"] == 2
    assert feed["last_error_type"] == error_type
    assert feed["last_polled_at"] == LATER + timedelta(minutes=5)
    assert feed["last_success_at"] == NOW
    # The stored incidents are untouched.
    stored = await incidents(session_factory, "anthropic")
    assert len(stored) == 10
    assert {row["fetched_at"] for row in stored} == {NOW}


async def test_a_success_after_failures_resets_the_count(session_factory: SessionFactory) -> None:
    server = FeedServer()
    server.answer(
        anthropic.url,
        httpx.Response(503),
        httpx.Response(200, content=fixture(ANTHROPIC_FEED)),
    )
    await _poll(session_factory, server, anthropic)

    await _poll(session_factory, server, anthropic, now=LATER)

    feed = await source(session_factory, "anthropic")
    assert (feed["last_outcome"], feed["consecutive_failures"]) == ("fetched", 0)
    assert feed["last_error_type"] is None


async def test_openai_json_failure_falls_back_to_rss_and_a_later_json_poll_upgrades_the_row(
    session_factory: SessionFactory,
) -> None:
    server = FeedServer()
    server.answer(openai.url, httpx.Response(200, content=b"<html>maintenance</html>"))
    server.answer(openai.fallback_url or "", httpx.Response(200, content=fixture(OPENAI_RSS)))

    fallback = await _poll(session_factory, server, openai)

    assert fallback.outcome == PollOutcome.FETCHED
    assert fallback.fell_back_from == PollOutcome.PARSE_ERROR
    assert fallback.source == "incidentio_rss"
    stored = await incidents(session_factory, "openai")
    assert len(stored) == 10
    assert {(row["source"], row["impact"]) for row in stored} == {("incidentio_rss", "unknown")}
    assert len(server.requests_to(openai.fallback_url or "")) == 1

    server.answer(openai.url, httpx.Response(200, content=fixture(OPENAI_FEED)))
    upgraded = await _poll(session_factory, server, openai, now=LATER)

    assert upgraded.fell_back_from is None
    rows = {row["external_id"]: row for row in await incidents(session_factory, "openai")}
    assert len(rows) == 10
    assert {row["source"] for row in rows.values()} == {"incidentio_json"}
    assert rows["01M506BA4XRSWH7THR53HQGN3Z"]["impact"] == "major"
    # The rows were updated in place, not duplicated.
    assert {row["id"] for row in stored} == {row["id"] for row in rows.values()}


async def test_rss_fallback_does_not_downgrade_a_json_row(session_factory: SessionFactory) -> None:
    server = FeedServer()
    server.answer(
        openai.url,
        httpx.Response(200, content=fixture(OPENAI_FEED)),
        httpx.Response(502),
    )
    server.answer(openai.fallback_url or "", httpx.Response(200, content=fixture(OPENAI_RSS)))
    await _poll(session_factory, server, openai)

    result = await _poll(session_factory, server, openai, now=LATER)

    assert result.fell_back_from == PollOutcome.HTTP_ERROR
    rows = {row["external_id"]: row for row in await incidents(session_factory, "openai")}
    row = rows["01M506BA4XRSWH7THR53HQGN3Z"]
    # The RSS carries no impact: the JSON's stays, and so does the row's source.
    assert (row["source"], row["impact"]) == ("incidentio_json", "major")
    assert row["fetched_at"] == LATER


async def test_rss_fallback_keeps_the_json_row_start_and_end(
    session_factory: SessionFactory,
) -> None:
    # An RSS item is the incident's newest update, so its `pubDate` is not the start.
    server = FeedServer()
    server.answer(
        openai.url,
        httpx.Response(200, content=fixture(OPENAI_FEED)),
        httpx.Response(502),
    )
    server.answer(openai.fallback_url or "", httpx.Response(200, content=fixture(OPENAI_RSS)))
    await _poll(session_factory, server, openai)
    before = {row["external_id"]: row for row in await incidents(session_factory, "openai")}

    await _poll(session_factory, server, openai, now=LATER)

    after = {row["external_id"]: row for row in await incidents(session_factory, "openai")}
    row, json_row = after["01M506BA4XRSWH7THR53HQGN3Z"], before["01M506BA4XRSWH7THR53HQGN3Z"]
    assert row["started_at"] == json_row["started_at"]
    assert row["started_at"].isoformat() == "2026-10-15T16:29:26+00:00"
    assert (row["status"], row["resolved_at"]) == ("resolved", json_row["resolved_at"])


async def test_rss_item_without_a_status_line_does_not_reopen_a_resolved_json_row(
    session_factory: SessionFactory,
) -> None:
    # An item whose description lost its `<b>Status: …</b>` line parses as `unknown`; the fallback
    # then leaves the status and end the JSON feed wrote alone.
    rss = fixture(OPENAI_RSS).replace(
        b"<b>Status: Resolved</b><br/><br/>All impacted", b"All impacted"
    )
    assert rss != fixture(OPENAI_RSS)
    server = FeedServer()
    server.answer(
        openai.url,
        httpx.Response(200, content=fixture(OPENAI_FEED)),
        httpx.Response(502),
    )
    server.answer(openai.fallback_url or "", httpx.Response(200, content=rss))
    await _poll(session_factory, server, openai)

    await _poll(session_factory, server, openai, now=LATER)

    rows = {row["external_id"]: row for row in await incidents(session_factory, "openai")}
    row = rows["01M506BA4XRSWH7THR53HQGN3Z"]
    assert (row["status"], row["resolved_at"].isoformat()) == (
        "resolved",
        "2026-10-15T18:56:00+00:00",
    )


async def test_a_successful_fallback_clears_the_validators_so_the_next_poll_is_unconditional(
    session_factory: SessionFactory,
) -> None:
    server = FeedServer()
    server.answer(
        openai.url,
        httpx.Response(200, content=fixture(OPENAI_FEED), headers={"etag": '"j1"'}),
        httpx.Response(502),
        # A 304 to a conditional request would keep the RSS-written values on the JSON rows.
        lambda request: (
            httpx.Response(304)
            if "if-none-match" in request.headers
            else httpx.Response(200, content=fixture(OPENAI_FEED))
        ),
    )
    server.answer(openai.fallback_url or "", httpx.Response(200, content=fixture(OPENAI_RSS)))
    await _poll(session_factory, server, openai)
    await _poll(session_factory, server, openai, now=LATER)
    assert (await source(session_factory, "openai"))["etag"] is None

    result = await _poll(session_factory, server, openai, now=LATER + timedelta(minutes=5))

    assert "if-none-match" not in server.requests_to(openai.url)[-1].headers
    assert result.outcome == PollOutcome.FETCHED
    rows = await incidents(session_factory, "openai")
    assert {(row["source"], row["fetched_at"]) for row in rows} == {
        ("incidentio_json", LATER + timedelta(minutes=5))
    }


async def test_a_failed_fallback_records_the_primary_outcome(
    session_factory: SessionFactory,
) -> None:
    server = FeedServer()
    server.answer(openai.url, httpx.Response(500))
    server.answer(openai.fallback_url or "", httpx.Response(500))

    result = await _poll(session_factory, server, openai)

    assert result.outcome == PollOutcome.HTTP_ERROR
    feed = await source(session_factory, "openai")
    assert (feed["last_outcome"], feed["consecutive_failures"]) == ("http_error", 1)
    assert await incidents(session_factory, "openai") == []


async def test_update_to_an_existing_incident_follows_the_feed(
    session_factory: SessionFactory,
) -> None:
    open_incident = first_anthropic_incident(status="investigating", resolved_at=None)
    resolved_at = "2026-10-16T12:30:00.000Z"
    closed = first_anthropic_incident(status="resolved", resolved_at=resolved_at)
    server = FeedServer()
    server.answer(
        anthropic.url,
        httpx.Response(200, content=anthropic_feed(open_incident)),
        httpx.Response(200, content=anthropic_feed(closed)),
    )
    await _poll(session_factory, server, anthropic)
    [before] = await incidents(session_factory, "anthropic")
    assert (before["status"], before["resolved_at"]) == ("investigating", None)

    await _poll(session_factory, server, anthropic, now=LATER)

    [after] = await incidents(session_factory, "anthropic")
    assert after["status"] == "resolved"
    assert after["resolved_at"].isoformat() == "2026-10-16T12:30:00+00:00"
    assert after["fetched_at"] == LATER
    assert (after["id"], after["first_seen_at"]) == (before["id"], before["first_seen_at"])


async def test_reopened_incident_clears_resolved_at(session_factory: SessionFactory) -> None:
    closed = first_anthropic_incident(status="resolved", resolved_at="2026-10-16T12:30:00.000Z")
    reopened = first_anthropic_incident(status="identified", resolved_at=None)
    server = FeedServer()
    server.answer(
        anthropic.url,
        httpx.Response(200, content=anthropic_feed(closed)),
        httpx.Response(200, content=anthropic_feed(reopened)),
    )
    await _poll(session_factory, server, anthropic)

    await _poll(session_factory, server, anthropic, now=LATER)

    [row] = await incidents(session_factory, "anthropic")
    assert (row["status"], row["resolved_at"]) == ("identified", None)


async def test_a_backdated_incident_never_starts_after_it_resolved(
    session_factory: SessionFactory,
) -> None:
    # incident.io's shape for an incident posted after it ended (seen live on 2026-10-21): no
    # `started_at`, `created_at` is the post time, and the updates' `display_at` hold the real
    # times. Without any `display_at` the start is clamped to the feed's own `resolved_at`.
    document = json.loads(fixture(OPENAI_FEED))
    updates = [
        ("resolved", "2026-10-20T17:35:00Z"),
        ("investigating", "2026-10-20T16:35:00Z"),
    ]
    backdated = {
        **document["incidents"][0],
        "id": "01M5G9QQ9V4CYDWFRYF9BWZ5GG",
        "status": "resolved",
        "created_at": "2026-10-21T22:36:29Z",
        "resolved_at": "2026-10-20T17:35:00Z",
        "incident_updates": [
            {
                "body": status,
                "status": status,
                "created_at": "2026-10-21T22:36:29Z",
                "display_at": at,
            }
            for status, at in updates
        ],
    }
    undated = {**backdated, "id": "01M5G9QQ9V4CYDWFRYF9BWZ5GH", "incident_updates": []}
    document["incidents"] = [backdated, undated]
    server = FeedServer()
    server.answer(openai.url, httpx.Response(200, content=json.dumps(document).encode()))

    await _poll(session_factory, server, openai)

    real_start, clamped = await incidents(session_factory, "openai")
    assert real_start["started_at"].isoformat() == "2026-10-20T16:35:00+00:00"
    assert real_start["resolved_at"].isoformat() == "2026-10-20T17:35:00+00:00"
    assert clamped["started_at"] == clamped["resolved_at"] == real_start["resolved_at"]


async def test_duplicate_ids_in_one_feed_upsert_once(session_factory: SessionFactory) -> None:
    earlier = first_anthropic_incident(name="Earlier title")
    later = first_anthropic_incident(name="Later title")
    server = FeedServer()
    server.answer(anthropic.url, httpx.Response(200, content=anthropic_feed(earlier, later)))

    result = await _poll(session_factory, server, anthropic)

    assert result.outcome == PollOutcome.FETCHED
    [row] = await incidents(session_factory, "anthropic")
    assert row["title"] == "Later title"
    assert (await source(session_factory, "anthropic"))["last_outcome"] == "fetched"


async def test_a_feed_over_the_incident_cap_is_a_parse_error_and_writes_nothing(
    session_factory: SessionFactory,
) -> None:
    # Small incidents, so the feed stays under the size cap and only the incident cap trips.
    small = {"incident_updates": [], "components": []}
    flood = [
        first_anthropic_incident(id=f"flood{index}", **small)
        for index in range(MAX_INCIDENTS_PER_FEED + 1)
    ]
    server = FeedServer()
    server.answer(anthropic.url, httpx.Response(200, content=anthropic_feed(*flood)))

    result = await _poll(session_factory, server, anthropic)

    assert result.outcome == PollOutcome.PARSE_ERROR
    assert result.error_type == "FeedParseError"
    assert await incidents(session_factory, "anthropic") == []
    assert (await source(session_factory, "anthropic"))["last_outcome"] == "parse_error"

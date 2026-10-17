"""The fixture status feed of `provider_incident:with_incident`: what the local server serves
parses through the real Anthropic adapter into one API-affected incident that overlaps the
scenario's spans, and the transport sends the feed request to the local server (loopback only,
no network)."""

from datetime import UTC, datetime, timedelta

import httpx

from accuracy.doctor.incident_feed import (
    FeedServer,
    LocalFeedTransport,
    fixture_feed,
    fixture_incident_id,
)
from app.provider_status.adapters.anthropic import anthropic

NOW = datetime(2026, 10, 16, 9, 30, tzinfo=UTC)
TITLE = "Spanlight Doctor harness fixture: elevated errors on the Claude API (ab12cd34)"


def _feed() -> bytes:
    return fixture_feed(
        external_id=fixture_incident_id("ab12cd34"),
        title=TITLE,
        started_at=NOW - timedelta(minutes=12),
        resolved_at=NOW,
    )


def test_fixture_feed_parses_as_one_api_affected_anthropic_incident() -> None:
    incidents, report = anthropic.parse_with_report(_feed())
    assert report.kept == 1
    (incident,) = incidents
    assert incident.title == TITLE
    assert incident.api_affected
    assert incident.started_at == NOW - timedelta(minutes=12)
    assert incident.resolved_at == NOW
    assert incident.status == "resolved"
    assert incident.url.startswith(anthropic.url_prefix)


def test_fixture_incident_ids_are_plain_and_marked_as_harness_rows() -> None:
    external_id = fixture_incident_id("ab12cd34")
    assert external_id.startswith("spanlight-doctor-")
    assert external_id == fixture_incident_id("ab12cd34")
    assert external_id != fixture_incident_id("ffff0000")


async def test_transport_serves_the_adapter_url_from_the_local_server() -> None:
    body = _feed()
    with FeedServer(body) as server:
        transport = LocalFeedTransport(server.port)
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.get(anthropic.url)
    assert response.status_code == 200
    assert response.content == body
    assert server.paths == ["/api/v2/incidents.json"]


async def test_transport_never_reaches_another_host() -> None:
    with FeedServer(_feed()) as server:
        transport = LocalFeedTransport(server.port)
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.get("https://example.com/anything")
    assert response.status_code == 404
    assert server.paths == []

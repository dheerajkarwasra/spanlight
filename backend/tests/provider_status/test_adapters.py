"""The status feed adapters parse recorded real feeds and refuse hostile ones."""

import copy
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.provider_status.adapters import ADAPTERS, FeedParseError, ParsedIncident, ParseReport
from app.provider_status.adapters.anthropic import anthropic
from app.provider_status.adapters.fields import MAX_INCIDENTS_PER_FEED
from app.provider_status.adapters.google import google
from app.provider_status.adapters.openai import openai
from app.provider_status.adapters.text import clamp, html_to_text

FIXTURES = Path(__file__).parent / "fixtures"


def _bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _json(name: str) -> Any:
    return json.loads(_bytes(name))


def _encode(document: Any) -> bytes:
    return json.dumps(document).encode()


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _anthropic_with(**changes: Any) -> bytes:
    """The first recorded Anthropic incident alone, with `changes` applied to it."""
    document = _json("anthropic-incidents-2026-10-16.json")
    incident = copy.deepcopy(document["incidents"][0])
    incident.update(changes)
    document["incidents"] = [incident]
    return _encode(document)


# --- registry ---------------------------------------------------------------------------------


def test_adapters_are_anthropic_openai_google_with_https_status_prefixes() -> None:
    assert [adapter.provider for adapter in ADAPTERS] == ["anthropic", "openai", "google"]
    for adapter in ADAPTERS:
        assert adapter.url.startswith("https://")
        assert adapter.url_prefix.startswith("https://status.")
        assert adapter.url_prefix.endswith("/")
    assert anthropic.url == "https://status.claude.com/api/v2/incidents.json"
    assert openai.url == "https://status.openai.com/api/v2/incidents.json"
    assert openai.fallback_url == "https://status.openai.com/history.rss"
    assert openai.fallback_source == "incidentio_rss"
    assert google.url == "https://status.cloud.google.com/incidents.json"


def test_adapters_without_a_fallback_refuse_to_parse_one() -> None:
    assert anthropic.fallback_url is None
    assert google.fallback_url is None
    with pytest.raises(FeedParseError):
        anthropic.parse_fallback(b"<rss/>")
    with pytest.raises(FeedParseError):
        google.parse_fallback(b"<rss/>")


# --- anthropic --------------------------------------------------------------------------------


def test_anthropic_fixture_parses_every_incident() -> None:
    incidents, report = anthropic.parse_with_report(_bytes("anthropic-incidents-2026-10-16.json"))
    assert len(incidents) == 10
    assert report == ParseReport(kept=10, skipped_naive=0, skipped_url=0)


def test_anthropic_first_incident_maps_every_field() -> None:
    first = anthropic.parse(_bytes("anthropic-incidents-2026-10-16.json"))[0]
    assert first == ParsedIncident(
        external_id="978mjkgw6mh7",
        title="Elevated errors on platform.claude.com",
        impact="major",
        status="resolved",
        api_affected=False,
        components=("Claude Console (platform.claude.com)",),
        started_at=_at("2026-10-07T13:25:04.155Z"),
        resolved_at=_at("2026-10-13T18:00:04.363Z"),
        url="https://status.claude.com/incidents/978mjkgw6mh7",
        latest_update="This issue has been resolved.",
        feed_updated_at=_at("2026-10-13T18:00:04.390Z"),
    )
    assert first.started_at.tzinfo is not None


def test_anthropic_started_at_falls_back_to_created_at() -> None:
    (incident,) = anthropic.parse(_anthropic_with(started_at=None))
    assert incident.started_at == _at("2026-10-07T13:25:04.163Z")


def test_anthropic_api_affected_needs_the_api_component() -> None:
    incidents = anthropic.parse(_bytes("anthropic-incidents-2026-10-16.json"))
    opus = incidents[3]
    assert "Claude API (api.anthropic.com)" in opus.components
    assert opus.api_affected is True
    claude_ai_only = _anthropic_with(components=[{"name": "claude.ai", "status": "operational"}])
    assert anthropic.parse(claude_ai_only)[0].api_affected is False
    no_components = _anthropic_with(components=[])
    assert anthropic.parse(no_components)[0].api_affected is False


def test_anthropic_unknown_vocabulary_becomes_unknown() -> None:
    (incident,) = anthropic.parse(_anthropic_with(status="exploded", impact="apocalyptic"))
    assert incident.status == "unknown"
    assert incident.impact == "unknown"


# --- openai json ------------------------------------------------------------------------------


def test_openai_fixture_parses_every_incident() -> None:
    incidents, report = openai.parse_with_report(_bytes("openai-incidents-2026-10-16.json"))
    assert len(incidents) == 10
    assert report == ParseReport(kept=10, skipped_naive=0, skipped_url=0)


def test_openai_first_incident_maps_every_field() -> None:
    first = openai.parse(_bytes("openai-incidents-2026-10-16.json"))[0]
    assert first.external_id == "01M4XNQX7C6RCBNGX9H711WFSG"
    assert first.title == "Delayed Costs data in the Compliance API"
    assert first.impact == "minor"
    assert first.status == "monitoring"
    assert first.components == ()
    assert first.started_at == _at("2026-10-14T17:00:44Z")
    assert first.resolved_at is None
    assert first.url == "https://status.openai.com/incidents/01M4XNQX7C6RCBNGX9H711WFSG"
    assert first.latest_update is not None
    assert first.latest_update.startswith("We have deployed a fix and are monitoring recovery.")
    assert first.feed_updated_at == _at("2026-10-15T22:36:42Z")


def test_openai_api_affected_uses_the_title_or_the_latest_update() -> None:
    incidents = openai.parse(_bytes("openai-incidents-2026-10-16.json"))
    by_id = {incident.external_id: incident for incident in incidents}
    assert by_id["01M4XNQX7C6RCBNGX9H711WFSG"].api_affected is True  # "Compliance API"
    assert by_id["01M4H7FKXDA425TDNP2CRGEYA3"].api_affected is False  # ChatGPT on Android
    document = _json("openai-incidents-2026-10-16.json")
    android = next(i for i in document["incidents"] if i["id"] == "01M4H7FKXDA425TDNP2CRGEYA3")
    android["incident_updates"].append(
        {"body": "Embeddings requests are also failing.", "created_at": "2026-10-11T00:00:00Z"}
    )
    document["incidents"] = [android]
    assert openai.parse(_encode(document))[0].api_affected is True


# --- openai rss fallback ----------------------------------------------------------------------


def test_openai_rss_fallback_parses_every_item() -> None:
    incidents, report = openai.parse_fallback_with_report(_bytes("openai-history-2026-10-16.rss"))
    assert len(incidents) == 10
    assert report == ParseReport(kept=10, skipped_naive=0, skipped_url=0)


def test_openai_rss_first_item_maps_every_field() -> None:
    first = openai.parse_fallback(_bytes("openai-history-2026-10-16.rss"))[0]
    assert first.external_id == "01M4XNQX7C6RCBNGX9H711WFSG"
    assert first.title == "Delayed Costs data in the Compliance API"
    assert first.impact == "unknown"
    assert first.status == "monitoring"
    assert first.components == ("Compliance API",)
    assert first.started_at == datetime(2026, 10, 15, 22, 36, 42, tzinfo=UTC)
    assert first.resolved_at is None
    assert first.url == "https://status.openai.com/incidents/01M4XNQX7C6RCBNGX9H711WFSG"
    assert first.latest_update is not None
    assert first.latest_update.startswith("We have deployed a fix and are monitoring recovery.")
    assert "Affected components" not in first.latest_update
    assert first.api_affected is True


def test_openai_rss_resolved_item_uses_its_components_for_api_affected() -> None:
    second = openai.parse_fallback(_bytes("openai-history-2026-10-16.rss"))[1]
    assert second.status == "resolved"
    assert second.components == ("Login",)
    assert second.api_affected is False
    assert second.resolved_at == second.started_at


def test_openai_rss_source_is_incidentio_rss() -> None:
    assert openai.source == "incidentio_json"
    assert openai.fallback_source == "incidentio_rss"


# --- google -----------------------------------------------------------------------------------


def test_google_keeps_only_vertex_ai_and_gemini_incidents() -> None:
    incidents, report = google.parse_with_report(_bytes("google-incidents-2026-10-16.json"))
    assert [incident.external_id for incident in incidents] == ["41E5S3mkTGDfkZuJZH5k"]
    assert report == ParseReport(kept=1, skipped_naive=0, skipped_url=0)


def test_google_incident_maps_every_field() -> None:
    (incident,) = google.parse(_bytes("google-incidents-2026-10-16.json"))
    assert incident.title.startswith("Vertex AI Gemini API customers experienced")
    assert incident.impact == "minor"  # SERVICE_INFORMATION
    assert incident.status == "resolved"
    assert incident.api_affected is True
    assert incident.components == (
        "Agent Assist",
        "Dialogflow CX",
        "Vertex Gemini API",
        "Google Cloud Support",
    )
    assert incident.started_at == _at("2026-02-27T12:37:00+00:00")
    assert incident.resolved_at == _at("2026-02-27T14:35:00+00:00")
    assert incident.url == "https://status.cloud.google.com/incidents/41E5S3mkTGDfkZuJZH5k"
    assert incident.latest_update is not None
    assert incident.latest_update.startswith("# Incident Report ## Summary")
    assert incident.feed_updated_at == _at("2026-03-09T05:25:43+00:00")


@pytest.mark.parametrize(
    ("status_impact", "end", "impact", "status"),
    [
        ("SERVICE_OUTAGE", None, "critical", "investigating"),
        ("SERVICE_DISRUPTION", None, "major", "investigating"),
        ("SERVICE_INFORMATION", "2026-02-27T14:35:00+00:00", "minor", "resolved"),
        ("SOMETHING_NEW", "2026-02-27T14:35:00+00:00", "unknown", "resolved"),
    ],
)
def test_google_maps_status_impact_and_end(
    status_impact: str, end: str | None, impact: str, status: str
) -> None:
    document = _json("google-incidents-2026-10-16.json")
    gemini = next(i for i in document if i["id"] == "41E5S3mkTGDfkZuJZH5k")
    gemini.update(status_impact=status_impact, end=end)
    (incident,) = google.parse(_encode([gemini]))
    assert (incident.impact, incident.status) == (impact, status)
    assert incident.resolved_at == (None if end is None else _at(end))


# --- hostile input ----------------------------------------------------------------------------


def test_entity_expansion_rss_is_refused() -> None:
    with pytest.raises(FeedParseError):
        openai.parse_fallback(_bytes("billion-laughs.rss"))


def test_malformed_rss_is_refused() -> None:
    with pytest.raises(FeedParseError):
        openai.parse_fallback(b"<rss><channel><item>")


def test_script_title_is_stored_as_text_without_tags() -> None:
    (incident,) = anthropic.parse(_bytes("script-title.json"))
    assert incident.title == "alert(1)"
    assert incident.components == ("Claude API (api.anthropic.com)",)
    assert incident.api_affected is True
    assert incident.latest_update == "We are looking into it . Details follow."


@pytest.mark.parametrize(
    "uri",
    [
        "javascript:alert(1)",
        "http://status.cloud.google.com/incidents/41E5S3mkTGDfkZuJZH5k",
        "https://evil.example/incidents/41E5S3mkTGDfkZuJZH5k",
    ],
)
def test_google_row_with_a_foreign_url_is_skipped(uri: str) -> None:
    document = _json("google-incidents-2026-10-16.json")
    gemini = next(i for i in document if i["id"] == "41E5S3mkTGDfkZuJZH5k")
    gemini["uri"] = uri
    incidents, report = google.parse_with_report(_encode([gemini]))
    assert incidents == []
    assert report == ParseReport(kept=0, skipped_naive=0, skipped_url=1)


@pytest.mark.parametrize(
    "link",
    [
        "javascript:alert(1)",
        "http://status.openai.com//incidents/01M4XNQX7C6RCBNGX9H711WFSG",
        "https://status.openai.com.evil.example/incidents/01M4XNQX7C6RCBNGX9H711WFSG",
    ],
)
def test_rss_item_with_a_foreign_link_is_skipped(link: str) -> None:
    good = "https://status.openai.com//incidents/01M4XNQX7C6RCBNGX9H711WFSG"
    body = _bytes("openai-history-2026-10-16.rss").decode().replace(good, link)
    incidents, report = openai.parse_fallback_with_report(body.encode())
    assert len(incidents) == 9
    assert report == ParseReport(kept=9, skipped_naive=0, skipped_url=1)


def test_statuspage_incident_whose_id_is_not_a_path_segment_is_skipped() -> None:
    incidents, report = anthropic.parse_with_report(_anthropic_with(id="javascript:alert(1)"))
    assert incidents == []
    assert report == ParseReport(kept=0, skipped_naive=0, skipped_url=1)


def test_naive_timestamp_skips_the_incident_and_is_reported() -> None:
    document = _json("anthropic-incidents-2026-10-16.json")
    document["incidents"][0]["started_at"] = "2026-10-07T13:25:04.155"
    document["incidents"][1]["resolved_at"] = "2026-10-07T21:23:29"
    incidents, report = anthropic.parse_with_report(_encode(document))
    assert len(incidents) == 8
    assert report == ParseReport(kept=8, skipped_naive=2, skipped_url=0)


def test_naive_rss_pub_date_skips_the_item() -> None:
    body = _bytes("openai-history-2026-10-16.rss").decode()
    body = body.replace("Thu, 15 Oct 2026 22:36:42 GMT", "Thu, 15 Oct 2026 22:36:42 -0000")
    incidents, report = openai.parse_fallback_with_report(body.encode())
    assert len(incidents) == 9
    assert report.skipped_naive == 1


def _rss(*, title: str, description: str) -> bytes:
    link = "https://status.openai.com//incidents/01M4XNQX7C6RCBNGX9H711WFSG"
    return (
        '<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel>'
        f"<item><title><![CDATA[{title}]]></title><link>{link}</link><guid>{link}</guid>"
        "<pubDate>Thu, 15 Oct 2026 22:36:42 GMT</pubDate>"
        f"<description><![CDATA[{description}]]></description></item>"
        "</channel></rss>"
    ).encode()


def test_unclosed_list_items_parse_in_linear_time() -> None:
    description = "<b>Status: Resolved</b><b>Affected components</b><ul>" + "<li>" * 50_000
    started = time.perf_counter()
    (incident,) = openai.parse_fallback(_rss(title="Outage", description=description))
    assert time.perf_counter() - started < 1
    assert incident.components == ()


@pytest.mark.parametrize(
    "description",
    [
        "<b>Status:" + " " * 60_000,
        "<b>Status:" + " " * 60_000 + "x",
        "<b>" + " " * 60_000 + "Affected components",
        "<b>Affected components</b><li>" + " " * 60_000 + "Login (" + " " * 60_000 + "</li>",
    ],
    ids=["unclosed-status", "status-then-text", "heading-spaces", "component-spaces"],
)
def test_long_whitespace_runs_parse_in_linear_time(description: str) -> None:
    started = time.perf_counter()
    openai.parse_fallback(_rss(title="Outage", description=description))
    assert time.perf_counter() - started < 1


def test_status_line_is_read_with_surrounding_whitespace() -> None:
    (incident,) = openai.parse_fallback(
        _rss(title="Outage", description="<b> Status:   Identified  </b><br/>Looking.")
    )
    assert incident.status == "identified"
    assert incident.latest_update == "Looking."


def test_list_items_are_found_in_any_case() -> None:
    description = (
        "<b>Status: Resolved</b><B>Affected Components</B>"
        "<UL><LI>Chat Completions (Partial outage)</LI><li>Files</li></UL>"
    )
    (incident,) = openai.parse_fallback(_rss(title="Outage", description=description))
    assert incident.components == ("Chat Completions", "Files")
    assert incident.api_affected is True


def test_non_ascii_component_names_keep_their_positions() -> None:
    description = (
        "<b>Status: Resolved</b><b>Affected components</b>"
        "<ul><LI>\u0130\u0130 Stra\u00dfe (Operational)</LI><li>Files</li>"
        "<li>Login(Operational)</li></ul>"
    )
    (incident,) = openai.parse_fallback(_rss(title="Outage", description=description))
    assert incident.components == ("\u0130\u0130 Stra\u00dfe", "Files", "Login")


def test_huge_description_is_bounded_before_parsing() -> None:
    description = "<b>Status: Monitoring</b>" + "x <li>" * 400_000 + "<b>Affected components</b>"
    started = time.perf_counter()
    (incident,) = openai.parse_fallback(_rss(title="Outage", description=description))
    assert time.perf_counter() - started < 1
    assert incident.status == "monitoring"
    assert incident.latest_update is not None
    assert len(incident.latest_update) == 2000


def test_statuspage_incident_whose_title_is_empty_after_stripping_is_skipped() -> None:
    document = _json("anthropic-incidents-2026-10-16.json")
    document["incidents"][0]["name"] = "<script></script>"
    incidents, report = anthropic.parse_with_report(_encode(document))
    assert len(incidents) == 9
    assert report == ParseReport(kept=9, skipped_naive=0, skipped_url=0, skipped_invalid=1)


def test_rss_item_whose_title_is_empty_is_skipped_not_fatal() -> None:
    body = _bytes("openai-history-2026-10-16.rss").decode()
    body = body.replace(
        "<![CDATA[Delayed Costs data in the Compliance API]]>", "<![CDATA[<b> </b>]]>", 1
    )
    incidents, report = openai.parse_fallback_with_report(body.encode())
    assert len(incidents) == 9
    assert report == ParseReport(kept=9, skipped_naive=0, skipped_url=0, skipped_invalid=1)


@pytest.mark.parametrize(
    ("field", "value"),
    [("id", "x" * 65), ("id", "../incidents"), ("external_desc", "<p></p>")],
)
def test_google_row_with_a_bad_id_or_empty_title_is_skipped(field: str, value: str) -> None:
    document = _json("google-incidents-2026-10-16.json")
    gemini = next(i for i in document if i["id"] == "41E5S3mkTGDfkZuJZH5k")
    gemini[field] = value
    incidents, report = google.parse_with_report(_encode([gemini]))
    assert incidents == []
    assert report == ParseReport(kept=0, skipped_naive=0, skipped_url=0, skipped_invalid=1)


@pytest.mark.parametrize(
    "uri", ["incidents/it's", "incidents/`x`", 'incidents/"x"', "incidents/<x>", "incidents/a b"]
)
def test_google_uri_with_quotes_brackets_or_spaces_is_skipped(uri: str) -> None:
    document = _json("google-incidents-2026-10-16.json")
    gemini = next(i for i in document if i["id"] == "41E5S3mkTGDfkZuJZH5k")
    gemini["uri"] = uri
    _, report = google.parse_with_report(_encode([gemini]))
    assert report.skipped_url == 1


def test_long_title_is_cut_to_500_characters() -> None:
    (incident,) = anthropic.parse(_anthropic_with(name="x" * 600))
    assert incident.title == "x" * 500


def test_components_and_latest_update_are_capped() -> None:
    components = [{"name": f"{i:03d}" + "c" * 200} for i in range(30)]
    updates = [{"body": "u" * 3000, "created_at": "2026-10-13T18:00:04.363Z"}]
    (incident,) = anthropic.parse(_anthropic_with(components=components, incident_updates=updates))
    assert len(incident.components) == 20
    assert all(len(name) == 120 for name in incident.components)
    assert incident.latest_update == "u" * 2000


def test_nul_characters_are_stripped() -> None:
    (incident,) = anthropic.parse(_anthropic_with(name="Elevated\x00 errors"))
    assert incident.title == "Elevated errors"


@pytest.mark.parametrize(
    "body",
    [b"not json", b"", b'{"incidents": [', b'{"incidents": NaN}', b"\xff\xfe"],
)
def test_body_that_is_not_json_is_refused(body: bytes) -> None:
    for adapter in ADAPTERS:
        with pytest.raises(FeedParseError):
            adapter.parse(body)


@pytest.mark.parametrize(
    "document",
    [{"page": {}}, {"incidents": {"id": "x"}}, [], {"incidents": ["not an object"]}],
)
def test_statuspage_document_without_an_incident_list_is_refused(document: Any) -> None:
    with pytest.raises(FeedParseError):
        anthropic.parse(_encode(document))
    with pytest.raises(FeedParseError):
        openai.parse(_encode(document))


def test_statuspage_incident_without_an_id_is_refused() -> None:
    document = _json("anthropic-incidents-2026-10-16.json")
    del document["incidents"][0]["id"]
    with pytest.raises(FeedParseError):
        anthropic.parse(_encode(document))


def test_unparseable_timestamp_is_refused() -> None:
    with pytest.raises(FeedParseError):
        anthropic.parse(_anthropic_with(created_at="yesterday", started_at=None))


def test_google_document_that_is_not_a_list_is_refused() -> None:
    with pytest.raises(FeedParseError):
        google.parse(b'{"incidents": []}')


# --- text helpers -----------------------------------------------------------------------------


def test_html_to_text_unescapes_strips_tags_and_collapses_whitespace() -> None:
    assert html_to_text("<b>Status:</b>&nbsp;Resolved<br/>\n\n  ok &amp; done") == (
        "Status: Resolved ok & done"
    )
    assert html_to_text("&lt;script&gt;alert(1)&lt;/script&gt;") == "alert(1)"
    assert html_to_text("trailing <scri") == "trailing"


def test_clamp_strips_nul_then_cuts() -> None:
    assert clamp("ab\x00cd", 3) == "abc"
    assert clamp("short", 10) == "short"


# --- incident cap ------------------------------------------------------------------------------


def _anthropic_feed_of(count: int) -> bytes:
    """A statuspage feed of `count` copies of the first recorded incident, each with its own id."""
    document = _json("anthropic-incidents-2026-10-16.json")
    first = document["incidents"][0]
    document["incidents"] = [{**first, "id": f"incident{index}"} for index in range(count)]
    return _encode(document)


def test_a_feed_of_exactly_the_cap_parses() -> None:
    incidents, report = anthropic.parse_with_report(_anthropic_feed_of(MAX_INCIDENTS_PER_FEED))
    assert len(incidents) == MAX_INCIDENTS_PER_FEED == 500
    assert report.kept == MAX_INCIDENTS_PER_FEED


def test_a_feed_over_the_incident_cap_is_refused_whole() -> None:
    # Ten times the real feeds' size is a compromised or broken page: nothing of it is stored.
    with pytest.raises(FeedParseError, match="more than 500 incidents"):
        anthropic.parse(_anthropic_feed_of(MAX_INCIDENTS_PER_FEED + 1))

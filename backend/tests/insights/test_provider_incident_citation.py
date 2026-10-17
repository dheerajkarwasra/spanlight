"""`provider_incident` cites the provider's overlapping status-page incident.

An incident only explains a finding: thresholds, severity and the fingerprint are the same with
and without one, so a misleading status page can neither open nor silence a finding.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.insights.catalogue import render
from app.insights.context import (
    Baselines,
    DetectorContext,
    LlmSpanRow,
    OrgProviderRow,
)
from app.insights.context_queries import load_context
from app.insights.detectors.provider_incident import detector
from app.insights.fingerprint import fingerprint
from app.insights.schemas import Finding, Window
from app.provider_status import service as provider_status
from app.provider_status.types import ProviderIncidentRow
from tests.jobs.bypass_support import (
    SessionFactory,
    execute,
    new_projects,
    seed_llm_spans,
)

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
WINDOW = Window(start=NOW - timedelta(minutes=30), end=NOW)
PROJECT = uuid.UUID("00000000-0000-0000-0000-00000000000a")
OTHER_PROJECT = uuid.UUID("00000000-0000-0000-0000-00000000000b")
NO_INCIDENT_NOTE = "No matching incident on the provider's status page."
INCIDENT_METRICS = ("incident_id", "incident_title", "incident_url", "incident_impact")


def _span(provider: str = "openai") -> LlmSpanRow:
    return LlmSpanRow(
        trace_id="a" * 32,
        span_id="b" * 16,
        session_id=None,
        external_user_id=None,
        environment=None,
        release=None,
        started_at=NOW - timedelta(minutes=5),
        duration_ms=120,
        status="error",
        error_class="provider_5xx",
        provider=provider,
        model="gpt-4.1",
        input_tokens=None,
        output_tokens=None,
        cached_tokens=None,
        cost_usd=None,
        request_hash=None,
        finish_reason=None,
        status_message=None,
    )


def _incident(
    *,
    provider: str = "openai",
    title: str = "Elevated error rates",
    impact: str = "major",
    started_at: datetime = NOW - timedelta(hours=1),
    resolved_at: datetime | None = None,
) -> ProviderIncidentRow:
    return ProviderIncidentRow(
        id=uuid.uuid4(),
        provider=provider,
        title=title,
        url=f"https://status.openai.com/incidents/{uuid.uuid4().hex[:8]}",
        impact=impact,
        started_at=started_at,
        resolved_at=resolved_at,
    )


def _context(
    incidents: Sequence[ProviderIncidentRow] = (), *, provider: str = "openai"
) -> DetectorContext:
    """30 5xx of 100 calls across two projects: a warning `provider_incident` on `provider`."""
    return DetectorContext(
        project_id=PROJECT,
        org_id=uuid.uuid4(),
        now=NOW,
        window=WINDOW,
        llm_spans=[_span(provider)],
        tool_spans=[],
        baselines=Baselines(),
        org_provider_errors=[
            OrgProviderRow(project_id=PROJECT, provider=provider, calls=50, provider_5xx=15),
            OrgProviderRow(project_id=OTHER_PROJECT, provider=provider, calls=50, provider_5xx=15),
        ],
        provider_incidents=incidents,
    )


def _one_finding(ctx: DetectorContext) -> Finding:
    findings = detector.run(ctx)
    assert len(findings) == 1
    return findings[0]


def _note(incident: ProviderIncidentRow) -> str:
    return f"Matches the provider's status page incident “{incident.title}” ({incident.impact})."


def _assert_cited(finding: Finding, incident: ProviderIncidentRow) -> None:
    metrics = finding.evidence.metrics
    assert metrics["incident_id"] == str(incident.id)
    assert metrics["incident_title"] == incident.title
    assert metrics["incident_url"] == incident.url
    assert metrics["incident_impact"] == incident.impact
    assert finding.params["incident_note"] == _note(incident)


def _assert_not_cited(finding: Finding) -> None:
    for name in INCIDENT_METRICS:
        assert name in finding.evidence.metrics
        assert finding.evidence.metrics[name] is None
    assert finding.params["incident_note"] == NO_INCIDENT_NOTE


def test_finding_with_and_without_incident_has_same_severity_and_fingerprint() -> None:
    without = _one_finding(_context())
    cited = _one_finding(_context([_incident()]))

    assert (cited.kind, cited.severity, cited.fingerprint_key) == (
        without.kind,
        without.severity,
        without.fingerprint_key,
    )
    assert fingerprint(PROJECT, cited.kind, cited.fingerprint_key) == fingerprint(
        PROJECT, without.kind, without.fingerprint_key
    )
    assert cited.evidence.trace_ids == without.evidence.trace_ids
    org_metrics = ("org_errors", "org_share", "projects_affected")
    assert {name: cited.evidence.metrics[name] for name in org_metrics} == {
        name: without.evidence.metrics[name] for name in org_metrics
    }


def test_incident_does_not_open_a_finding_below_the_thresholds() -> None:
    below = DetectorContext(
        project_id=PROJECT,
        org_id=uuid.uuid4(),
        now=NOW,
        window=WINDOW,
        llm_spans=[_span()],
        tool_spans=[],
        baselines=Baselines(),
        org_provider_errors=[
            OrgProviderRow(project_id=PROJECT, provider="openai", calls=100, provider_5xx=5)
        ],
        provider_incidents=[_incident(impact="critical")],
    )

    assert detector.run(below) == []


def test_incident_metrics_and_note_are_set_when_one_overlaps() -> None:
    incident = _incident()

    _assert_cited(_one_finding(_context([incident])), incident)
    _assert_not_cited(_one_finding(_context()))


def test_newest_overlapping_incident_wins() -> None:
    older = _incident(title="Older", started_at=NOW - timedelta(hours=3))
    newer = _incident(title="Newer", started_at=NOW - timedelta(minutes=40))
    latest = _incident(
        title="Latest",
        started_at=NOW - timedelta(minutes=10),
        resolved_at=NOW - timedelta(minutes=5),
    )

    _assert_cited(_one_finding(_context([older, latest, newer])), latest)
    _assert_cited(_one_finding(_context([older, newer])), newer)


def test_incident_of_another_provider_is_ignored() -> None:
    finding = _one_finding(_context([_incident(provider="anthropic")]))

    _assert_not_cited(finding)


def test_provider_matches_the_feed_case_insensitively() -> None:
    incident = _incident(provider="openai")

    finding = _one_finding(_context([incident], provider="OpenAI"))

    _assert_cited(finding, incident)
    assert finding.fingerprint_key == "OpenAI"  # the key keeps the span's spelling


def test_incident_starting_after_the_window_is_ignored() -> None:
    finding = _one_finding(_context([_incident(started_at=NOW)]))

    _assert_not_cited(finding)


@pytest.mark.parametrize(("minutes_before", "cited"), [(14, True), (16, False)])
def test_grace_window_boundary(minutes_before: int, cited: bool) -> None:
    incident = _incident(
        started_at=NOW - timedelta(hours=2),
        resolved_at=WINDOW.start - timedelta(minutes=minutes_before),
    )

    finding = _one_finding(_context([incident]))

    if cited:
        _assert_cited(finding, incident)
    else:
        _assert_not_cited(finding)


def test_summary_ends_with_the_note() -> None:
    incident = _incident()
    cited = _one_finding(_context([incident]))
    without = _one_finding(_context())

    assert render(cited).summary.endswith(f" {_note(incident)}")
    assert render(without).summary.endswith(f" {NO_INCIDENT_NOTE}")
    assert render(without).summary == (
        "30 5xx responses (30.0 % of calls) from openai in the last 30 minutes across 2 projects. "
        + NO_INCIDENT_NOTE
    )


_INSERT_INCIDENT = (
    "INSERT INTO provider_incidents (id, provider, source, external_id, title, impact, status, "
    "api_affected, started_at, resolved_at, url, fetched_at) "
    "VALUES (:id, :provider, 'statuspage_json', :external_id, :title, 'major', 'investigating', "
    "true, :started_at, NULL, :url, :started_at)"
)


async def _seed_incident(
    session_factory: SessionFactory, provider: str, started_at: datetime
) -> uuid.UUID:
    incident_id = uuid.uuid4()
    await execute(
        session_factory,
        _INSERT_INCIDENT,
        {
            "id": incident_id,
            "provider": provider,
            "external_id": incident_id.hex,
            "title": f"{provider} errors",
            "started_at": started_at,
            "url": f"https://status.{provider}.com/incidents/{incident_id.hex}",
        },
    )
    return incident_id


async def _seed_failing_spans(
    session_factory: SessionFactory,
    project: uuid.UUID,
    now: datetime,
    provider: str,
    count: int = 3,
) -> None:
    """`count` `provider_5xx` llm spans of `provider`, five minutes ago."""
    await seed_llm_spans(
        session_factory, project, count, started_at=now - timedelta(minutes=5), status="error"
    )
    await execute(
        session_factory,
        "UPDATE spans SET error_class = 'provider_5xx', provider = :provider "
        "WHERE project_id = :project_id",
        {"project_id": project, "provider": provider},
    )


async def test_load_context_reads_incidents_without_bypass(
    session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    (project,) = await new_projects(session_factory, 1)
    await _seed_failing_spans(session_factory, project, now, "openai")
    cited = await _seed_incident(session_factory, "openai", now - timedelta(minutes=20))
    await _seed_incident(session_factory, "anthropic", now - timedelta(minutes=20))

    settings_seen: list[tuple[str | None, str | None]] = []
    real_read = provider_status.overlapping_api_incidents

    async def spy(db: AsyncSession, **kwargs: Any) -> list[ProviderIncidentRow]:
        bypass = await db.scalar(text("SELECT current_setting('app.bypass_rls', true)"))
        bound = await db.scalar(text("SELECT current_setting('app.project_id', true)"))
        settings_seen.append((bypass, bound))
        return await real_read(db, **kwargs)

    monkeypatch.setattr(provider_status, "overlapping_api_incidents", spy)

    async with session_factory() as db:
        ctx = await load_context(db, project, now)
        await db.commit()

    assert ctx is not None
    assert [incident.id for incident in ctx.provider_incidents] == [cited]
    # One read, for the one provider with 5xx errors, bound to the project with no bypass.
    assert len(settings_seen) == 1
    bypass, bound = settings_seen[0]
    assert bypass in (None, "")
    assert bound == str(project)


async def test_load_context_matches_the_feed_provider_case_insensitively(
    session_factory: SessionFactory,
) -> None:
    now = datetime.now(UTC)
    (project,) = await new_projects(session_factory, 1)
    # 50 errors in one project: enough for a finding without a second project.
    await _seed_failing_spans(session_factory, project, now, "OpenAI", count=50)
    cited = await _seed_incident(session_factory, "openai", now - timedelta(minutes=20))

    async with session_factory() as db:
        ctx = await load_context(db, project, now)
        await db.commit()

    assert ctx is not None
    assert [incident.id for incident in ctx.provider_incidents] == [cited]
    finding = _one_finding(ctx.sliced(Window(start=now - timedelta(minutes=30), end=now)))
    assert finding.evidence.metrics["incident_id"] == str(cited)

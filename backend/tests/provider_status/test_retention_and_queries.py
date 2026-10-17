"""Incident retention and the reads the API and the detectors build on."""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.jobs.tasks.retention import run_retention
from app.provider_status.retention import (
    PROVIDER_INCIDENT_RETENTION_DAYS,
    apply_provider_incident_retention,
)
from app.provider_status.service import (
    list_incidents,
    list_sources,
    overlapping_api_incidents,
)
from tests.gateway_support import logged
from tests.provider_status.poll_support import (
    NOW,
    SessionFactory,
    execute,
    rows,
    task_context,
)


@dataclass(frozen=True)
class _Window:
    start: datetime
    end: datetime


async def _incident(
    session_factory: SessionFactory,
    external_id: str,
    started_at: datetime,
    *,
    resolved_at: datetime | None = None,
    provider: str = "anthropic",
    api_affected: bool = True,
) -> uuid.UUID:
    incident_id = uuid.uuid4()
    await execute(
        session_factory,
        "INSERT INTO provider_incidents (id, provider, source, external_id, title, impact, "
        "status, api_affected, started_at, resolved_at, url, fetched_at) "
        "VALUES (:id, :provider, 'statuspage_json', :external_id, 'Elevated errors', 'major', "
        "'resolved', :api_affected, :started_at, :resolved_at, "
        "'https://status.claude.com/incidents/' || :external_id, :started_at)",
        {
            "id": incident_id,
            "provider": provider,
            "external_id": external_id,
            "api_affected": api_affected,
            "started_at": started_at,
            "resolved_at": resolved_at,
        },
    )
    return incident_id


async def test_retention_deletes_incidents_older_than_730_days(
    session_factory: SessionFactory, settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    assert PROVIDER_INCIDENT_RETENTION_DAYS == 730
    now = datetime.now(UTC)
    await _incident(session_factory, "old", now - timedelta(days=731))
    await _incident(session_factory, "kept", now - timedelta(days=729))

    assert await apply_provider_incident_retention(session_factory, now=now) == 1

    remaining = await rows(session_factory, "SELECT external_id FROM provider_incidents")
    assert remaining == [{"external_id": "kept"}]

    await _incident(session_factory, "old-again", now - timedelta(days=800))
    caplog.set_level(logging.INFO)
    await run_retention(task_context(session_factory, settings), {})
    [line] = logged(caplog, "retention_applied")
    assert line["provider_incidents_deleted"] == 1


async def test_list_incidents_selects_by_overlap_provider_and_api_only(
    session_factory: SessionFactory,
) -> None:
    window = _Window(NOW - timedelta(hours=24), NOW)
    before = await _incident(
        session_factory, "before", NOW - timedelta(days=3), resolved_at=NOW - timedelta(days=2)
    )
    spanning = await _incident(
        session_factory, "spanning", NOW - timedelta(days=2), resolved_at=NOW - timedelta(hours=1)
    )
    ongoing = await _incident(session_factory, "ongoing", NOW - timedelta(hours=2))
    not_api = await _incident(
        session_factory, "not-api", NOW - timedelta(hours=3), api_affected=False
    )
    other = await _incident(session_factory, "other", NOW - timedelta(hours=4), provider="openai")
    after = await _incident(session_factory, "after", NOW + timedelta(hours=1))

    async with session_factory() as db:
        api = await list_incidents(
            db, window=window, provider=None, api_only=True, limit=100, after=None
        )
        everything = await list_incidents(
            db, window=window, provider=None, api_only=False, limit=100, after=None
        )
        openai_only = await list_incidents(
            db, window=window, provider="openai", api_only=True, limit=100, after=None
        )

    assert [row.id for row in api] == [ongoing, other, spanning]
    assert [row.id for row in everything] == [ongoing, not_api, other, spanning]
    assert [row.id for row in openai_only] == [other]
    assert before not in {row.id for row in everything}
    assert after not in {row.id for row in everything}


async def test_list_incidents_pages_by_started_at_and_id(session_factory: SessionFactory) -> None:
    window = _Window(NOW - timedelta(hours=24), NOW)
    same_time = NOW - timedelta(hours=1)
    ids = [await _incident(session_factory, f"i{n}", same_time) for n in range(3)]
    newest = await _incident(session_factory, "newest", NOW - timedelta(minutes=10))

    seen: list[uuid.UUID] = []
    after = None
    async with session_factory() as db:
        while True:
            page = await list_incidents(
                db, window=window, provider=None, api_only=True, limit=2, after=after
            )
            seen += [row.id for row in page]
            if len(page) < 2:
                break
            after = (page[-1].started_at, page[-1].id)

    assert seen == [newest, *sorted(ids, reverse=True)]


async def test_overlapping_api_incidents_applies_the_grace(session_factory: SessionFactory) -> None:
    start, end = NOW - timedelta(minutes=30), NOW
    cited = await _incident(
        session_factory,
        "cited",
        NOW - timedelta(hours=2),
        resolved_at=start - timedelta(minutes=14),
    )
    await _incident(
        session_factory,
        "too-old",
        NOW - timedelta(hours=2),
        resolved_at=start - timedelta(minutes=16),
    )
    ongoing = await _incident(session_factory, "ongoing", NOW - timedelta(hours=1))
    await _incident(session_factory, "not-api", NOW - timedelta(hours=1), api_affected=False)
    await _incident(session_factory, "openai", NOW - timedelta(hours=1), provider="openai")

    async with session_factory() as db:
        found = await overlapping_api_incidents(
            db, provider="anthropic", start=start, end=end, now=NOW
        )

    assert [row.id for row in found] == [ongoing, cited]
    assert found[0].url == "https://status.claude.com/incidents/ongoing"
    assert (found[0].title, found[0].impact) == ("Elevated errors", "major")


async def test_list_sources_returns_the_three_feeds(session_factory: SessionFactory) -> None:
    async with session_factory() as db:
        sources = await list_sources(db)

    assert [row.provider for row in sources] == ["anthropic", "google", "openai"]
    assert {row.consecutive_failures for row in sources} == {0}
    assert {row.last_polled_at for row in sources} == {None}

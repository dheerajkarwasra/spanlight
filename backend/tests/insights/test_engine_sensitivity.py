"""The engine applies the project's sensitivity before it stores findings, and keeps each
insight's strongest reading."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.models import DetectorRun, Insight
from app.db.rls import bind_project
from app.insights.context import DetectorContext
from app.insights.engine import run_project
from app.insights.schemas import Evidence, Finding, Severity

Sessions = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)


@dataclass
class FakeErrorSpike:
    """Returns one `error_spike` finding per (key, error rate) it is given."""

    rates: dict[str, str] = field(default_factory=dict)
    kind: str = "error_spike"
    window: timedelta = timedelta(minutes=15)
    min_samples: int = 1

    def run(self, ctx: DetectorContext) -> list[Finding]:
        return [
            Finding(
                kind=self.kind,
                severity=Severity.WARNING,
                fingerprint_key=key,
                evidence=Evidence(
                    metrics={"error_rate": Decimal(rate)},
                    window={"start": ctx.window.start, "end": ctx.window.end},
                ),
                params={"error_rate": rate, "model": "m", "environment": key},
            )
            for key, rate in self.rates.items()
        ]


async def _project(sessions: Sessions, level: str | None = None) -> uuid.UUID:
    org_id, project_id = uuid.uuid4(), uuid.uuid4()
    async with sessions() as db, db.begin():
        await db.execute(
            text("INSERT INTO organizations (id, name, slug) VALUES (:id, 'Org', :slug)"),
            {"id": org_id, "slug": f"org-{org_id.hex[:12]}"},
        )
        await db.execute(
            text("INSERT INTO projects (id, org_id, name, slug) VALUES (:id, :org, 'P', 'p')"),
            {"id": project_id, "org": org_id},
        )
        if level is not None:
            await bind_project(db, project_id)
            await db.execute(
                text(
                    "INSERT INTO detector_sensitivity (project_id, detector, level) "
                    "VALUES (:project_id, 'error_spike', :level)"
                ),
                {"project_id": project_id, "level": level},
            )
    return project_id


async def _run(
    sessions: Sessions,
    settings: Settings,
    project_id: uuid.UUID,
    detector: FakeErrorSpike,
    now: datetime,
) -> int | None:
    async with sessions() as db:
        results = await run_project(db, project_id, now, (detector,), settings=settings)
    return results[0].findings


async def test_balanced_drops_weaker_finding_and_counts_kept(
    session_factory: Sessions, settings: Settings
) -> None:
    project_id = await _project(session_factory, "balanced")
    detector = FakeErrorSpike({"weak": "0.05", "strong": "0.30"})

    assert await _run(session_factory, settings, project_id, detector, NOW) == 1

    async with session_factory() as db, db.begin():
        await bind_project(db, project_id)
        stored = (await db.scalars(select(DetectorRun.findings))).all()
        titles = (await db.scalars(select(Insight.title))).all()
    assert stored == [1]
    assert len(titles) == 1 and "strong" in titles[0]


async def test_peak_strength_keeps_the_maximum(
    session_factory: Sessions, settings: Settings
) -> None:
    project_id = await _project(session_factory)
    await _run(session_factory, settings, project_id, FakeErrorSpike({"a": "0.30"}), NOW)
    later = NOW + timedelta(minutes=5)
    await _run(session_factory, settings, project_id, FakeErrorSpike({"a": "0.12"}), later)

    async with session_factory() as db, db.begin():
        await bind_project(db, project_id)
        row = (await db.execute(select(Insight.peak_strength, Insight.occurrences))).one()
    assert row == (Decimal("0.3000"), 2)

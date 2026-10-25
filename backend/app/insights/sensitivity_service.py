"""Doctor sensitivity for the API: each check's setting, a preview of every setting, and a change.

Counts come from stored findings, not from replaying detectors (spec ruling 7): a finding counts
at a setting when its strongest reading met that setting's gate. The strongest reading is the
larger of `insights.peak_strength` and the strength of the stored evidence (rows from before
`peak_strength` have only the second); a finding with neither always counts, as the engine never
hides a finding on missing data. Every query runs in a transaction bound to the project
(row-level security); the explicit `project_id` filters are a second guard.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ProblemError
from app.db.models import AuditAction, DetectorSensitivity, Insight
from app.insights.sensitivity import (
    ALL_DETECTORS,
    DEFAULT_LEVEL,
    GATES,
    TUNABLE,
    Level,
    gate_for,
    strength,
)
from app.insights.service import Actor
from app.services.audit import record_audit

LEVELS: tuple[Level, ...] = ("high_recall", "balanced", "high_precision")
WINDOW_DAYS = (7, 30)
LIST_DAYS = 30


@dataclass(frozen=True, slots=True)
class Gates:
    balanced: str
    high_precision: str


@dataclass(frozen=True, slots=True)
class SensitivityItem:
    detector: str
    tunable: bool
    level: Level
    metric_label: str | None
    gates: Gates | None
    findings_30d: int
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class WindowCounts:
    days: int
    high_recall: int
    balanced: int
    high_precision: int


@dataclass(frozen=True, slots=True)
class _Reading:
    kind: str
    strongest: Decimal | None
    last_seen_at: datetime


def not_tunable() -> ProblemError:
    return ProblemError(
        404, "DETECTOR_NOT_TUNABLE", "This check has one setting, or there is no such check."
    )


async def list_items(
    db: AsyncSession, project_id: uuid.UUID, now: datetime
) -> list[SensitivityItem]:
    """Every check in catalogue order, with its setting and its findings in the last 30 days."""
    rows = await db.scalars(
        select(DetectorSensitivity).where(DetectorSensitivity.project_id == project_id)
    )
    saved = {row.detector: row for row in rows}
    readings = await _readings(db, project_id, now - timedelta(days=LIST_DAYS))
    items = []
    for kind in ALL_DETECTORS:
        row = saved.get(kind)
        # The migration's CHECK holds `level` to the three values of `Level`.
        level = cast(Level, row.level) if row else DEFAULT_LEVEL
        items.append(_item(kind, level, row.updated_at if row else None, readings))
    return items


async def preview_counts(
    db: AsyncSession, project_id: uuid.UUID, kind: str, now: datetime
) -> list[WindowCounts]:
    """How many of the kind's findings each setting would have kept, over 7 and 30 days."""
    if kind not in TUNABLE:
        raise not_tunable()
    readings = await _readings(db, project_id, now - timedelta(days=max(WINDOW_DAYS)), kind)
    windows = []
    for days in WINDOW_DAYS:
        since = now - timedelta(days=days)
        shown = [reading for reading in readings if reading.last_seen_at >= since]
        counts = [_count(kind, level, shown) for level in LEVELS]
        windows.append(WindowCounts(days, *counts))
    return windows


async def set_level(
    db: AsyncSession,
    project_id: uuid.UUID,
    kind: str,
    level: Level,
    actor: Actor,
    now: datetime,
) -> SensitivityItem:
    """Save the check's setting and audit the change. Setting the level it already has writes
    nothing and audits nothing, so a repeated request leaves one event."""
    if kind not in TUNABLE:
        raise not_tunable()
    # The route's `lock_project_of` (a NO KEY UPDATE lock on the project and org) serializes
    # changes, so two admins changing one check audit the level each really replaced.
    row = await db.scalar(
        select(DetectorSensitivity)
        .where(DetectorSensitivity.project_id == project_id, DetectorSensitivity.detector == kind)
        .with_for_update()
    )
    before = row.level if row else DEFAULT_LEVEL
    updated_at = row.updated_at if row else None
    if level != before:
        changed = {"level": level, "updated_by": actor.user_id, "updated_at": now}
        await db.execute(
            insert(DetectorSensitivity)
            .values(project_id=project_id, detector=kind, **changed)
            .on_conflict_do_update(index_elements=["project_id", "detector"], set_=changed)
        )
        await record_audit(
            db,
            org_id=actor.org_id,
            actor_user_id=actor.user_id,
            action=AuditAction.INSIGHT_SENSITIVITY_CHANGE,
            target_type="detector_sensitivity",
            target_id=kind,
            ip=actor.ip,
            project_id=project_id,
            metadata={"from": before, "to": level},
        )
        updated_at = now
    readings = await _readings(db, project_id, now - timedelta(days=LIST_DAYS), kind)
    return _item(kind, level, updated_at, readings)


async def _readings(
    db: AsyncSession, project_id: uuid.UUID, since: datetime, kind: str | None = None
) -> list[_Reading]:
    """The strongest reading of each of the project's insights seen since `since`, any status.

    Bounded by the project's insights in the window: one row per fingerprint, not per detection.
    """
    query = select(
        Insight.kind, Insight.evidence["metrics"], Insight.peak_strength, Insight.last_seen_at
    ).where(Insight.project_id == project_id, Insight.last_seen_at >= since)
    if kind is not None:
        query = query.where(Insight.kind == kind)
    rows = await db.execute(query)
    return [
        _Reading(row_kind, _strongest(row_kind, metrics, peak), seen)
        for row_kind, metrics, peak, seen in rows
    ]


def _strongest(kind: str, metrics: Any, peak: Decimal | None) -> Decimal | None:
    found = [
        value
        for value in (peak, strength(kind, metrics if isinstance(metrics, Mapping) else {}))
        if value is not None
    ]
    return max(found) if found else None


def _count(kind: str, level: Level, readings: Sequence[_Reading]) -> int:
    gate = gate_for(kind, level)
    return sum(
        1
        for reading in readings
        if reading.kind == kind
        and (gate is None or reading.strongest is None or reading.strongest >= gate)
    )


def _item(
    kind: str, level: Level, updated_at: datetime | None, readings: Sequence[_Reading]
) -> SensitivityItem:
    gate = GATES.get(kind)
    return SensitivityItem(
        detector=kind,
        tunable=gate is not None,
        level=level,
        metric_label=gate.metric_label if gate else None,
        gates=Gates(str(gate.balanced), str(gate.high_precision)) if gate else None,
        findings_30d=_count(kind, level, readings),
        updated_at=updated_at,
    )

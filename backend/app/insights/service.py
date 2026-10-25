"""The insight lifecycle: apply a detector's findings, and the actions people take on an insight.
Callers commit.

Every call runs in a transaction bound to the project (row-level security), or with the
worker's bypass. `apply_findings` follows this table, for one detector's kind at a time:

| Current status            | Detected again                       | Not detected for 24 h     |
|---------------------------|--------------------------------------|---------------------------|
| none                      | insert `open`, occurrences 1: opened | -                         |
| `open`, `acknowledged`    | bump occurrences, evidence, copy     | `resolved`                |
| `resolved`                | `open`, bump: opened                 | -                         |
| `muted`, mute running     | bump silently, stays muted           | stays muted               |
| `muted`, mute ended       | `open`, bump, mute cleared: opened   | `resolved`                |

Severity follows the latest detection. Only a transition into `open` is "opened", so a
re-detection never notifies twice. The caller only applies the findings of a detector that ran
without error: a failed run says nothing about whether its problems went away.

The actions each write an audit event: `acknowledge` (`open` only), `resolve` (anything not
resolved; a later detection reopens it), `mute` (any status, until a time in the next 90 days,
with a reason) and `unmute` (`muted` only, back to `open`). An action on a status it does not
apply to is `409 INVALID_TRANSITION`; a bad mute is `422 INVALID_MUTE`; an insight that is not
in the project is `404`. Audit metadata holds the kind, the status before and the mute's end
and reason; insights carry no secrets.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ProblemError, not_found
from app.core.ids import new_id
from app.db.models import AuditAction, Insight
from app.insights import lifecycle_queries as queries
from app.insights.catalogue import render
from app.insights.fingerprint import fingerprint
from app.insights.schemas import Finding, InsightStatus
from app.insights.sensitivity import strength
from app.services.audit import record_audit

MAX_MUTE = timedelta(days=90)
MAX_MUTE_REASON = 500


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """What one batch of findings did: the insights it opened (new or reopened), how many it
    updated without opening, and how many of the kind's insights it resolved as quiet."""

    opened: list[uuid.UUID] = field(default_factory=list)
    bumped: int = 0
    resolved: int = 0


@dataclass(frozen=True, slots=True)
class Actor:
    """Who takes an action, in which organization, and from where (for the audit event)."""

    org_id: uuid.UUID
    user_id: uuid.UUID
    ip: str | None = None


async def apply_findings(
    db: AsyncSession,
    project_id: uuid.UUID,
    kind: str,
    findings: Sequence[Finding],
    now: datetime,
) -> ApplyResult:
    """Record one run's findings for `kind` and resolve the kind's quiet insights.

    Two findings with the same fingerprint key are one problem; the later one wins. Raises
    `ValueError` for a finding of another kind (a detector bug).
    """
    rows: dict[str, dict[str, Any]] = {}
    for finding in findings:
        if finding.kind != kind:
            raise ValueError(f"a {finding.kind!r} finding was applied as {kind!r}")
        row = _new_row(project_id, finding, now)
        rows[row["fingerprint"]] = row
    before = await queries.lock_by_fingerprint(db, project_id, sorted(rows))
    written = await queries.upsert_detections(db, list(rows.values()), now)
    # Opened: a new row, or one that was not open before and is now. A row another transaction
    # inserted after the lock has no status before; it was opened there, not here.
    opened = [
        row.id
        for row in written
        if row.status == InsightStatus.OPEN
        and (row.inserted or before.get(row.fingerprint, InsightStatus.OPEN) != InsightStatus.OPEN)
    ]
    opened_ids = set(opened)
    bumped = sum(1 for row in written if not row.inserted and row.id not in opened_ids)
    resolved = await queries.resolve_quiet(db, project_id, kind, sorted(rows), now)
    return ApplyResult(opened=opened, bumped=bumped, resolved=resolved)


def _new_row(project_id: uuid.UUID, finding: Finding, now: datetime) -> dict[str, Any]:
    """The `insights` row a first detection of `finding` writes, copy rendered from the
    catalogue."""
    copy = render(finding)
    return {
        "id": new_id(),
        "project_id": project_id,
        "kind": finding.kind,
        "severity": finding.severity,
        "status": InsightStatus.OPEN,
        "fingerprint": fingerprint(project_id, finding.kind, finding.fingerprint_key),
        "title": copy.title,
        "summary": copy.summary,
        "failure_layer": copy.failure_layer.value,
        "certainty": copy.certainty.value,
        "evidence": finding.evidence.model_dump(mode="json"),
        "suggested_fix": copy.suggested_fix,
        "verification": copy.verification,
        "first_seen_at": now,
        "last_seen_at": now,
        "occurrences": 1,
        "peak_strength": strength(finding.kind, finding.evidence.metrics),
    }


async def acknowledge(
    db: AsyncSession,
    project_id: uuid.UUID,
    insight_id: uuid.UUID,
    actor: Actor,
    *,
    now: datetime,
) -> Insight:
    """`open` → `acknowledged`, recording who and when."""
    insight = await _lock(db, project_id, insight_id)
    before = insight.status
    if before is not InsightStatus.OPEN:
        raise _invalid_transition("acknowledge", before)
    insight.status = InsightStatus.ACKNOWLEDGED
    insight.acknowledged_by = actor.user_id
    insight.acknowledged_at = now
    await db.flush()
    await _audit(db, insight, AuditAction.INSIGHT_ACKNOWLEDGE, actor, before)
    return insight


async def resolve(
    db: AsyncSession,
    project_id: uuid.UUID,
    insight_id: uuid.UUID,
    actor: Actor,
    *,
    now: datetime,
) -> Insight:
    """Any status but `resolved` → `resolved`. A mute ends with it."""
    insight = await _lock(db, project_id, insight_id)
    before = insight.status
    if before is InsightStatus.RESOLVED:
        raise _invalid_transition("resolve", before)
    insight.status = InsightStatus.RESOLVED
    insight.resolved_at = now
    insight.muted_until = None
    insight.mute_reason = None
    await db.flush()
    await _audit(db, insight, AuditAction.INSIGHT_RESOLVE, actor, before)
    return insight


async def mute(
    db: AsyncSession,
    project_id: uuid.UUID,
    insight_id: uuid.UUID,
    actor: Actor,
    until: datetime,
    reason: str,
    *,
    now: datetime,
) -> Insight:
    """Any status → `muted` until `until`. Muting a muted insight replaces its end and reason.

    While the mute runs, detections update the insight silently; when it ends, the next
    detection reopens it, or 24 quiet hours resolve it. Raises `404`, then `422 INVALID_MUTE`.
    """
    insight = await _lock(db, project_id, insight_id)
    reason = _check_mute(until, reason, now)
    before = insight.status
    insight.status = InsightStatus.MUTED
    insight.muted_until = until
    insight.mute_reason = reason
    insight.resolved_at = None
    await db.flush()
    await _audit(
        db,
        insight,
        AuditAction.INSIGHT_MUTE,
        actor,
        before,
        extra={"until": until.isoformat(), "reason": reason},
    )
    return insight


async def unmute(
    db: AsyncSession,
    project_id: uuid.UUID,
    insight_id: uuid.UUID,
    actor: Actor,
) -> Insight:
    """`muted` → `open`, unacknowledged."""
    insight = await _lock(db, project_id, insight_id)
    before = insight.status
    if before is not InsightStatus.MUTED:
        raise _invalid_transition("unmute", before)
    insight.status = InsightStatus.OPEN
    insight.muted_until = None
    insight.mute_reason = None
    insight.acknowledged_by = None
    insight.acknowledged_at = None
    await db.flush()
    await _audit(db, insight, AuditAction.INSIGHT_UNMUTE, actor, before)
    return insight


async def _lock(db: AsyncSession, project_id: uuid.UUID, insight_id: uuid.UUID) -> Insight:
    insight = await queries.lock_insight(db, project_id, insight_id)
    if insight is None:
        raise not_found()
    return insight


def _check_mute(until: datetime, reason: str, now: datetime) -> str:
    """The reason, stripped, when the mute is valid; otherwise `422 INVALID_MUTE`."""
    if until <= now:
        raise _invalid_mute("The mute must end in the future.")
    if until - now > MAX_MUTE:
        raise _invalid_mute("The mute must end at most 90 days from now.")
    stripped = reason.strip()
    if not 1 <= len(stripped) <= MAX_MUTE_REASON:
        raise _invalid_mute(f"The reason must be 1 to {MAX_MUTE_REASON} characters.")
    return stripped


def _invalid_mute(detail: str) -> ProblemError:
    return ProblemError(422, "INVALID_MUTE", detail)


def _invalid_transition(action: str, status: InsightStatus) -> ProblemError:
    return ProblemError(
        409, "INVALID_TRANSITION", f"An insight that is {status.value} cannot {action}."
    )


async def _audit(
    db: AsyncSession,
    insight: Insight,
    action: AuditAction,
    actor: Actor,
    before: InsightStatus,
    *,
    extra: dict[str, object] | None = None,
) -> None:
    """The audit event for an action: the insight's kind and its status before, plus `extra`."""
    await record_audit(
        db,
        org_id=actor.org_id,
        actor_user_id=actor.user_id,
        action=action,
        target_type="insight",
        target_id=insight.id,
        ip=actor.ip,
        metadata={"kind": insight.kind, "status_before": before.value, **(extra or {})},
    )

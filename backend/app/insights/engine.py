"""Run the detectors over one project: load its context once, then each detector in turn.

Each detector gets the context sliced to its own window and runs inside `try`. Its run row, the
insights its findings open or bump, the quiet insights of its kind that resolve, and the
notifications for critical openings commit together in one transaction per detector. A detector
that raises is recorded with its error, its kind is not auto-resolved in that run (nothing says
the problem went away), and the next detector still runs.

The project's sensitivity (`app.insights.sensitivity`) is read once per run with the context. A
finding below its check's setting is dropped before anything is stored, so the run row counts
only the findings kept, and an open insight that falls below the setting resolves as quiet.
"""

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.observability import DETECTOR_DURATION, DETECTOR_RUNS
from app.db.models import DetectorRun
from app.db.rls import bind_project
from app.insights.context import DetectorContext
from app.insights.context_queries import load_context
from app.insights.detectors import (
    cache_opportunity,
    client_timeout_misconfigured,
    context_growth,
    cost_spike,
    error_spike,
    latency_regression,
    model_retiring,
    provider_incident,
    rate_limit_pressure,
    retry_after_ignored,
    retry_storm,
    tool_loop,
    truncated_outputs,
    truncated_stream_accepted,
    unpriced_spend,
    unsupported_parameter_retried,
)
from app.insights.detectors.base import Detector
from app.insights.notify import notify_opened
from app.insights.schemas import Finding, Window
from app.insights.sensitivity import DEFAULT_LEVEL, Level, passes
from app.insights.sensitivity_queries import load_levels
from app.insights.service import apply_findings

if TYPE_CHECKING:
    from app.config import Settings

logger = structlog.get_logger(__name__)

DETECTORS: tuple[Detector, ...] = (
    error_spike.detector,
    latency_regression.detector,
    cost_spike.detector,
    retry_storm.detector,
    retry_after_ignored.detector,
    rate_limit_pressure.detector,
    truncated_outputs.detector,
    context_growth.detector,
    cache_opportunity.detector,
    tool_loop.detector,
    truncated_stream_accepted.detector,
    unsupported_parameter_retried.detector,
    client_timeout_misconfigured.detector,
    unpriced_spend.detector,
    provider_incident.detector,
    model_retiring.detector,
)
"""Every detector, in catalogue order."""

MAX_ERROR_LENGTH = 500


@dataclass(frozen=True, slots=True)
class DetectorRunResult:
    """One detector's run over one project. `findings` is None exactly when it failed."""

    detector: str
    findings: int | None
    error: str | None
    duration_ms: int
    opened: int = 0
    notified: int = 0
    recorded: bool = True  # False only when even the error row could not be written


async def run_project(
    db: AsyncSession,
    project_id: uuid.UUID,
    now: datetime,
    detectors: tuple[Detector, ...] = DETECTORS,
    *,
    settings: "Settings",
    progress: list[DetectorRunResult] | None = None,
) -> list[DetectorRunResult]:
    """Run `detectors` over the project at `now`; empty when the project is gone. Commits.

    Each result is also appended to `progress` as soon as it exists, so a caller that cancels
    the run (a time bound) knows which detectors already recorded theirs.
    """
    ctx = await load_context(db, project_id, now)
    levels = {} if ctx is None else await load_levels(db, project_id)
    await db.commit()
    results = progress if progress is not None else []
    if ctx is None:
        return results
    for detector in detectors:
        level = levels.get(detector.kind, DEFAULT_LEVEL)
        results.append(await _run_detector(db, ctx, detector, level, settings=settings))
    return results


async def record_failed_runs(
    db: AsyncSession,
    project_id: uuid.UUID,
    now: datetime,
    detectors: Sequence[Detector],
    error: str,
    duration_ms: int,
) -> None:
    """One error row per detector of a project whose run failed outside the detectors (the
    context read, a time bound), so the failure shows in detector health and the project moves
    to the back of the stalest-first order. Commits."""
    await bind_project(db, project_id)
    for detector in detectors:
        window = Window(start=now - detector.window, end=now)
        db.add(_run_row(project_id, detector, window, None, False, duration_ms, error))
    await db.commit()
    for detector in detectors:
        DETECTOR_RUNS.labels(detector=detector.kind, outcome="error").inc()


async def _run_detector(
    db: AsyncSession,
    ctx: DetectorContext,
    detector: Detector,
    level: Level,
    *,
    settings: "Settings",
) -> DetectorRunResult:
    window = Window(start=ctx.now - detector.window, end=ctx.now)
    started = time.perf_counter()
    findings: list[Finding] | None = None
    error: str | None = None
    try:
        findings = [
            finding
            for finding in detector.run(ctx.sliced(window))
            if passes(detector.kind, level, finding.evidence.metrics)
        ]
    except Exception as exc:  # a detector bug must not stop the others
        error = error_text(exc)
        logger.error("detector_failed", **_log_fields(ctx, detector, exc), exc_info=exc)
    elapsed = time.perf_counter() - started
    DETECTOR_DURATION.labels(detector=detector.kind).observe(elapsed)
    result = DetectorRunResult(
        detector=detector.kind,
        findings=None if findings is None else len(findings),
        error=error,
        duration_ms=round(elapsed * 1000),
    )
    try:
        result = await _record(db, ctx, detector, window, result, findings, settings=settings)
    except Exception as exc:  # the findings could not be stored: record the run as failed
        logger.error(
            "detector_findings_not_applied", **_log_fields(ctx, detector, exc), exc_info=exc
        )
        result = await _record_error(db, ctx, detector, window, result.duration_ms, exc)
    DETECTOR_RUNS.labels(
        detector=detector.kind, outcome="ok" if result.error is None else "error"
    ).inc()
    return result


async def _record(
    db: AsyncSession,
    ctx: DetectorContext,
    detector: Detector,
    window: Window,
    result: DetectorRunResult,
    findings: list[Finding] | None,
    *,
    settings: "Settings",
) -> DetectorRunResult:
    """Write the run row, then apply and notify the findings, in one transaction. Commits."""
    await bind_project(db, ctx.project_id)
    opened: list[uuid.UUID] = []
    notified = 0
    if findings is not None:
        applied = await apply_findings(db, ctx.project_id, detector.kind, findings, ctx.now)
        opened = applied.opened
        notified = await notify_opened(db, ctx.project_id, opened, settings=settings)
    db.add(
        _run_row(
            ctx.project_id,
            detector,
            window,
            result.findings,
            ctx.truncated,
            result.duration_ms,
            result.error,
        )
    )
    await db.commit()
    return DetectorRunResult(
        detector=result.detector,
        findings=result.findings,
        error=result.error,
        duration_ms=result.duration_ms,
        opened=len(opened),
        notified=notified,
    )


async def _record_error(
    db: AsyncSession,
    ctx: DetectorContext,
    detector: Detector,
    window: Window,
    duration_ms: int,
    exc: Exception,
) -> DetectorRunResult:
    """Roll back the failed write and record the run as failed in a fresh transaction. If even
    that fails (the database is unreachable), log it and return an unrecorded result, so the
    project's remaining detectors still get their turn."""
    result = DetectorRunResult(
        detector=detector.kind, findings=None, error=error_text(exc), duration_ms=duration_ms
    )
    try:
        await db.rollback()
        await bind_project(db, ctx.project_id)
        db.add(
            _run_row(
                ctx.project_id, detector, window, None, ctx.truncated, duration_ms, result.error
            )
        )
        await db.commit()
    except Exception as write_exc:  # nothing left to record into; keep going
        logger.error(
            "detector_run_not_recorded", **_log_fields(ctx, detector, write_exc), exc_info=write_exc
        )
        await _quiet_rollback(db)
        return replace(result, recorded=False)
    return result


async def _quiet_rollback(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except Exception as exc:  # the connection is already gone
        logger.warning("detector_rollback_failed", error_type=type(exc).__name__, exc_info=exc)


def _run_row(
    project_id: uuid.UUID,
    detector: Detector,
    window: Window,
    findings: int | None,
    truncated: bool,
    duration_ms: int,
    error: str | None,
) -> DetectorRun:
    return DetectorRun(
        project_id=project_id,
        detector=detector.kind,
        window_start=window.start,
        window_end=window.end,
        findings=findings,
        truncated=truncated,
        duration_ms=duration_ms,
        error=error,
    )


def error_text(exc: BaseException) -> str:
    """`ClassName: first line of the message`, at most 500 characters.

    Only the first line: a database error's later lines carry the statement and its parameters.
    """
    lines = str(exc).strip().splitlines()
    message = f"{type(exc).__name__}: {lines[0]}" if lines else type(exc).__name__
    return message[:MAX_ERROR_LENGTH]


def _log_fields(ctx: DetectorContext, detector: Detector, exc: Exception) -> dict[str, str]:
    return {
        "project_id": str(ctx.project_id),
        "detector": detector.kind,
        "error_type": type(exc).__name__,
    }

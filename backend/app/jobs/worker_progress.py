"""Monotonic short-loop evidence, independent of heartbeat and long-job activity."""

import time
from typing import Literal

import structlog

from app.core.observability import (
    WORKER_CLAIM_POLL_AGE,
    WORKER_PHASE,
    WORKER_PHASE_AGE,
    WORKER_STALLED,
    WORKER_STALLS,
)

Phase = Literal["schedule", "claim", "short_execution", "complete", "fail", "retry", "idle"]
# One second cleanup margin exceeds the measured local maximum; these are internal bounds.
_PHASE_ALLOWANCE: dict[Phase, float] = {
    "schedule": 2.0,
    "claim": 1.5,
    "short_execution": 51.0,
    "complete": 2.0,
    "fail": 2.0,
    "retry": 2.0,
    "idle": 3.0,
}
# 50 s handler + 1 s schedule + .5 s claim + 1 s terminal + 2 s idle + 1 s cleanup
# + 10 s watchdog cadence. Failed polls cannot refresh this clock.
POLL_STALL_SECONDS = 65.5
logger = structlog.get_logger(__name__)


class ShortLoopProgress:
    def __init__(self) -> None:
        self.phase: Phase = "idle"
        self.phase_since = time.monotonic()
        self.last_poll = self.phase_since
        self.stalled = False
        self.enter("idle")
        self.report()

    def enter(self, phase: Phase) -> None:
        self.phase = phase
        self.phase_since = time.monotonic()
        for value in _PHASE_ALLOWANCE:
            WORKER_PHASE.labels(value).set(int(value == phase))
        WORKER_PHASE_AGE.set(0)

    def poll_succeeded(self) -> None:
        """An empty committed claim is progress too; a failed claim never calls this."""
        self.last_poll = time.monotonic()
        WORKER_CLAIM_POLL_AGE.set(0)

    def report(self) -> None:
        now = time.monotonic()
        phase_age = now - self.phase_since
        poll_age = now - self.last_poll
        stalled = phase_age > _PHASE_ALLOWANCE[self.phase] or poll_age > POLL_STALL_SECONDS
        WORKER_PHASE_AGE.set(phase_age)
        WORKER_CLAIM_POLL_AGE.set(poll_age)
        WORKER_STALLED.set(int(stalled))
        if stalled and not self.stalled:
            WORKER_STALLS.labels(self.phase).inc()
            logger.warning(
                "worker_short_loop_stalled",
                phase=self.phase,
                phase_age_seconds=phase_age,
                claim_poll_age_seconds=poll_age,
            )
        self.stalled = stalled

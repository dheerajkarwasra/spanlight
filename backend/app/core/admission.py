"""In-process admission cap for the dashboard API and ingestion (`/api/`, `/v1/`).

Past the throughput one process can sustain, requests do not fail, they queue: each waits for a
pooled connection and then for the event loop. At about 1.5 times that point the loop falls so
far behind that uvicorn stops accepting connections, Caddy answers 502 after its dial timeout
and clients wait up to 30 seconds. With a cap on the requests in flight, the ones past it are
answered 503 problem+json with `Retry-After: 1` at once, without reading their body or touching
the database, and well-behaved clients (the SDK honours `Retry-After`) back off.

`/gw/` is exempt: the gateway has its own back-pressure (key limits, budgets), and a model call
is held open for seconds by the provider, not by this process's work. `/health/` is exempt so
an orchestrator never restarts a process for being busy.

Not uvicorn's `--limit-concurrency`: it answers a bare text 503 with no `Retry-After`, outside
the problem+json contract, and counts health checks and gateway streams too.
"""

import time

import structlog
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.errors import problem_response
from app.core.observability import ADMISSION_REJECTIONS

logger = structlog.get_logger(__name__)

ADMITTED_PREFIXES = ("/api/", "/v1/")
# Overload lasts as long as the burst, so the warning is logged at most once a minute;
# `spanlight_admission_rejections_total` counts every rejection.
_REJECTED_LOG_INTERVAL_SECONDS = 60.0


class AdmissionMiddleware:
    """Answers 503 to `/api/` and `/v1/` requests while `limit` of them are already in flight.

    The count is a plain integer: one event loop per process, so no lock is needed.
    """

    def __init__(self, app: ASGIApp, limit: int) -> None:
        self.app = app
        self.limit = limit
        self.in_flight = 0
        self._last_log: float | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(ADMITTED_PREFIXES):
            await self.app(scope, receive, send)
            return
        if self.in_flight >= self.limit:
            ADMISSION_REJECTIONS.inc()
            now = time.monotonic()
            if self._last_log is None or now - self._last_log >= _REJECTED_LOG_INTERVAL_SECONDS:
                self._last_log = now
                logger.warning("admission_rejected", limit=self.limit, path=scope["path"])
            response = problem_response(
                503,
                "SERVICE_UNAVAILABLE",
                "The service is too busy to answer right now. Try again in a moment.",
                headers={"Retry-After": "1"},
            )
            await response(scope, receive, send)
            return
        self.in_flight += 1
        try:
            await self.app(scope, receive, send)
        finally:
            self.in_flight -= 1

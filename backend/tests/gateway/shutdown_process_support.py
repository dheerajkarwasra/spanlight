"""Disposable 15C calibration process, not a production server or signal adapter."""

import asyncio
import json
import signal
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.config import Settings
from app.core.idempotency_store import reserve
from app.db.session import create_engine, create_session_factory
from app.gateway import recorder, span_write
from app.gateway.execute import execute
from app.gateway.http import build_http_client
from app.gateway.in_process import context_for_project
from app.gateway.runtime import build_runtime, join_runtime
from app.ingest import pipeline
from app.telemetry_losses import flush
from tests.gateway.support import chat_request
from tests.gateway_fixtures import CREDENTIALS_KEYS
from tests.helpers import new_trace_id, span


async def main(data: dict[str, Any]) -> None:
    loop = asyncio.get_running_loop()
    stopped = asyncio.Event()
    loop.add_signal_handler(signal.SIGTERM, stopped.set)
    settings = Settings(
        database_url=data["database_url"],
        credentials_keys=CREDENTIALS_KEYS,
        gateway_allow_insecure_base_urls=True,
        gateway_record_concurrency=1,
    )
    engine = create_engine(str(settings.database_url))
    sessions = create_session_factory(engine)
    http = build_http_client(settings)
    runtime = build_runtime(settings, sessions, http=http, rate_limit_sessions=sessions)
    flush.start(sessions)
    async with sessions() as db, db.begin():
        context = await context_for_project(
            db, uuid.UUID(data["project_id"]), route_id=None, environment="test", services=runtime
        )
    await reserve(sessions, "user:shutdown", "unknown-call", b"x" * 32)
    result = await execute(chat_request(stream=True), context)
    assert result.stream is not None
    await anext(result.stream)
    real_ingest = pipeline.ingest_spans
    entered = asyncio.Event()
    pid = 0

    async def locked_ingest(db: Any, target: Any, spans: Any, **kwargs: Any) -> Any:
        nonlocal pid
        if spans[0]["name"] == "shutdown-blocked":
            pid = await db.scalar(text("SELECT pg_backend_pid()"))
            entered.set()
            await db.execute(text("SELECT pg_advisory_xact_lock(150039)"))
        return await real_ingest(db, target, spans, **kwargs)

    pytest.MonkeyPatch().setattr(span_write, "ingest_spans", locked_ingest)
    recorder.record_soon(context, span(trace_id=new_trace_id(), name="shutdown-blocked"))
    await entered.wait()
    print("TASK15C_READY " + json.dumps({"pid": pid}), flush=True)
    await stopped.wait()
    started = loop.time()
    await result.stream.aclose()
    upstream_closed = loop.time()
    joined = await join_runtime(runtime, cancel_at=loop.time(), deadline=started + 2)
    assert joined.joined and joined.accounting_complete
    writers_joined = loop.time()
    assert await flush.stop(timeout_s=1, deadline=started + 2)
    losses_flushed = loop.time()
    await http.aclose()
    http_closed = loop.time()
    await engine.dispose()
    disposed = loop.time()
    print(
        "TASK15C_RESULT "
        + json.dumps(
            {
                "upstream_close_s": upstream_closed - started,
                "recorder_join_s": writers_joined - upstream_closed,
                "loss_flush_s": losses_flushed - writers_joined,
                "http_close_s": http_closed - losses_flushed,
                "dispose_s": disposed - http_closed,
                "total_s": disposed - started,
                "lost": recorder.spans_lost(),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main(json.loads(sys.stdin.readline())))

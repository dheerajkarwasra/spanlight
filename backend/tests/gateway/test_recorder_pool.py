"""Recorder pool isolation in GatewayContext, GatewayRuntime, and span_write."""

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx

from app.config import Settings
from app.db.models import GatewayKey
from app.gateway.context import GatewayContext, context_for_key
from app.gateway.key_context import KeyContext
from app.gateway.recorder import _settle_lost, _writers_for
from app.gateway.route_config import RouteConfig
from app.gateway.runtime import build_runtime
from app.gateway.span_write import write_span
from app.ingest.pipeline import IngestOutcome, IngestTarget


def test_runtime_wires_dedicated_recorder_sessions() -> None:
    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://u:p@localhost/db")
    request_sessions = MagicMock(name="request_sessions")
    rate_sessions = MagicMock(name="rate_sessions")
    recorder_sessions = MagicMock(name="recorder_sessions")
    runtime = build_runtime(
        settings,
        request_sessions,
        http=httpx.AsyncClient(),
        rate_limit_sessions=rate_sessions,
        recorder_sessions=recorder_sessions,
    )
    assert runtime.recorder_sessions is recorder_sessions

    key_row = MagicMock(spec=GatewayKey)
    key_row.environment = "prod"
    key = KeyContext(
        key=key_row,
        project_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        capture_payloads=True,
        redact_contact_info=False,
        route_id=uuid.uuid4(),
        route_name="default",
        route_version=1,
        route=RouteConfig.model_validate(
            {"targets": [{"credential_id": str(uuid.uuid4())}], "retry": {}, "fallback": {}}
        ),
        credentials={},
    )
    ctx = context_for_key(key, runtime)
    assert ctx.sessions is request_sessions
    assert ctx.recorder_sessions is recorder_sessions
    assert ctx.write_sessions is recorder_sessions


def test_write_span_uses_recorder_pool_instead_of_request_pool(monkeypatch: Any) -> None:
    calls: list[str] = []

    def make_factory(label: str) -> Any:
        @asynccontextmanager
        async def _begin() -> Any:
            yield

        session = MagicMock()
        session.begin = _begin
        session.execute = AsyncMock()
        session.scalar = AsyncMock(return_value=None)

        @asynccontextmanager
        async def _factory() -> Any:
            calls.append(label)
            yield session

        return _factory

    request_factory = make_factory("request")
    recorder_factory = make_factory("recorder")

    settings = Settings(_env_file=None, database_url="postgresql+asyncpg://u:p@localhost/db")
    ctx = GatewayContext(
        key=None,
        project_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        environment="prod",
        route=RouteConfig.model_validate(
            {"targets": [{"credential_id": str(uuid.uuid4())}], "retry": {}, "fallback": {}}
        ),
        route_id=uuid.uuid4(),
        route_name="default",
        route_version=1,
        credentials={},
        capture_payloads=True,
        budget_guard=MagicMock(),
        cache=MagicMock(),
        http=MagicMock(),
        sessions=request_factory,
        recorder_sessions=recorder_factory,
        settings=settings,
    )

    monkeypatch.setattr("app.gateway.span_write.bind_project", AsyncMock())
    monkeypatch.setattr(
        "app.gateway.span_write.ingest_spans", AsyncMock(return_value=IngestOutcome(accepted=1))
    )
    monkeypatch.setattr("app.gateway.span_write.settle", AsyncMock())
    monkeypatch.setattr("app.gateway.span_write.settle_run", AsyncMock())
    monkeypatch.setattr("app.gateway.span_write.settle_unstored", AsyncMock(), raising=False)

    async def run() -> None:
        target = IngestTarget(project_id=ctx.project_id, capture_payloads=True, org_id=ctx.org_id)
        span = {"span_id": "s1", "trace_id": "t1", "status": "ok", "attributes": {}}
        outcome = await write_span(ctx, target, span, (), ctx.clock())
        assert outcome.accepted == 1
        assert calls == ["recorder"]

        # Verify _settle_lost schedules against write_sessions (recorder pool) too.
        writers = _writers_for(ctx)
        assert writers is not None
        monkeypatch.setattr("app.gateway.recorder.settle_unstored", AsyncMock())
        _settle_lost(writers, ctx, span)
        await asyncio.gather(*list(writers.pending))

    asyncio.run(run())

"""Application factory: `uvicorn app.main:app`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.alerts.channels import register_channel_deliverers
from app.api import gw, health, ingest, scores_ingest, v1
from app.api.deps import Clock, utcnow
from app.api.gw_errors import install_gateway_error_handlers
from app.api.key_cache import KeyCache
from app.api.v1.prompts_read import key_router as prompt_read_router
from app.config import Settings, get_settings
from app.core.admission import AdmissionMiddleware
from app.core.body_limit import BodyLimitMiddleware
from app.core.egress import Resolver
from app.core.errors import install_error_handlers
from app.core.idempotency import install_idempotency
from app.core.logging import configure_logging
from app.core.middleware import (
    BearerRequestMiddleware,
    OriginCheckMiddleware,
    RequestContextMiddleware,
)
from app.core.sentry import init_sentry
from app.core.tracing import configure_tracing, shutdown_tracing
from app.db.session import create_engine, create_session_factory
from app.ee import hooks as ee
from app.gateway.http import build_http_client
from app.gateway.runtime import GatewayRuntime, build_runtime, close_runtime
from app.gateway.settle_helpers import drain_settling
from app.notifications.defaults import register_default_deliverers
from app.pricing.cost import sync_seed_prices
from app.telemetry_losses import flush as loss_flush

logger = structlog.get_logger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    oauth_transport: httpx.AsyncBaseTransport | None = None,
    gateway_transport: httpx.AsyncBaseTransport | None = None,
    gateway_resolver: Resolver | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """Build the app.

    The keyword arguments exist for tests: `oauth_transport` replaces the network for sign-in
    provider calls, `gateway_transport` for calls to LLM providers (gateway traffic and
    credential checks), `gateway_resolver` the DNS that those calls (and alert webhook URLs, when
    saved) are vetted against, and `clock` replaces the time that one-time codes and login
    challenges are judged against, so a test decides which code is valid. The same clock drives
    the gateway request path (key limits, the budget guard, the cache and span times) and the
    `now` the budgets API reports a budget's period and spend at.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    init_sentry(settings)
    # The api delivers too: a channel's test-send claims its outbox rows and sends them in the
    # request. Registering again replaces the earlier deliverers, so building a second app is safe.
    register_default_deliverers(settings)

    engine = _request_engine(settings)
    session_factory = create_session_factory(engine)
    alert_http = register_channel_deliverers(session_factory, settings)
    # Idempotency keys get a pool of their own: they take a second connection while the request
    # still holds one from the main pool. No overflow, and a short wait before giving up.
    idempotency_engine = create_engine(
        settings.database_url,
        pool_size=settings.idempotency_pool_size,
        max_overflow=0,
        pool_timeout=settings.idempotency_pool_timeout_seconds,
    )
    rate_limit_engine = _rate_limit_engine(settings)
    rate_limit_session_factory = create_session_factory(rate_limit_engine)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        await _sync_prices(session_factory)
        # Built whatever GATEWAY_MODE says: credential checks use the client, and in-process
        # callers the runtime, even when `/gw/*` is served by a standalone gateway.
        runtime = _start_gateway(
            application,
            settings,
            session_factory,
            rate_limit_session_factory,
            transport=gateway_transport,
            resolver=gateway_resolver,
        )
        yield
        # Explanations whose request went away settle in the background; they need the engine.
        await drain_settling()
        # The server has stopped taking requests and let in-flight ones finish (each stream's
        # `aclose()` queues its span), so the spans are drained while the engine is still open.
        await _stop_gateway(runtime)
        await alert_http.aclose()
        await engine.dispose()
        await idempotency_engine.dispose()
        await rate_limit_engine.dispose()
        await shutdown_tracing(getattr(application.state, "tracer_provider", None))

    app = FastAPI(
        title="Spanlight API",
        version="0.1.0",
        docs_url="/api/docs" if settings.is_api_docs_enabled else None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.idempotency_engine = idempotency_engine
    app.state.idempotency_session_factory = create_session_factory(idempotency_engine)
    app.state.oauth_transport = oauth_transport
    # The DNS that alert webhook URLs are vetted against when saved; None is the system resolver.
    app.state.egress_resolver = gateway_resolver
    app.state.clock = clock or utcnow
    app.state.ee_license = ee.load(settings, app.state.clock())
    app.state.rate_limit_engine = rate_limit_engine
    app.state.rate_limit_session_factory = rate_limit_session_factory
    app.state.key_cache = KeyCache(settings.api_key_cache_ttl_seconds)

    install_error_handlers(app)
    embedded_gateway = settings.gateway_mode == "embedded"
    if embedded_gateway:
        # After the dashboard's handlers, which it wraps: problem+json everywhere but `/gw/`.
        install_gateway_error_handlers(app)
    # First, so it is the innermost middleware: it sees the response a route's error became.
    install_idempotency(app)
    # Before the middleware below: its attribute middleware must be inside the request-id one.
    configure_tracing(settings, app, engine)
    # Outside the idempotency middleware (which reads bodies) and inside the ones below, so a
    # refusal is logged with a request id.
    app.add_middleware(BodyLimitMiddleware)
    # Outside the body limit and idempotency layers, so a rejected request's body is never read,
    # and inside the request-id one, so a rejection carries a request id and is counted.
    app.add_middleware(AdmissionMiddleware, limit=settings.api_max_in_flight)
    # Added last = outermost: every response, including Origin rejections,
    # carries a request id and is logged.
    app.add_middleware(OriginCheckMiddleware, allowed_origins=settings.origin_allowlist)
    app.add_middleware(BearerRequestMiddleware)
    app.add_middleware(RequestContextMiddleware)

    # Hot ingestion and health paths first: FastAPI walks routers in mount order.
    for router in (
        health.router,
        ingest.router,
        scores_ingest.router,
        prompt_read_router,
        v1.router,
        *ee.routers(),
    ):
        app.include_router(router)
    # In `standalone` and `disabled` mode `/gw/*` stays unmounted and answers 404 problem+json.
    if embedded_gateway:
        app.include_router(gw.router)
    return app


def create_gateway_app(
    settings: Settings | None = None,
    *,
    gateway_transport: httpx.AsyncBaseTransport | None = None,
    gateway_resolver: Resolver | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """The standalone gateway (`python -m app.gateway`): `/gw/v1/*`, `/health/*` and `/metrics`.

    Same image and settings as the api, run with `GATEWAY_MODE=standalone` on the api so the
    two do not both serve `/gw/*`. None of the dashboard's middleware is installed: no
    cookies, CSRF, Origin check or idempotency keys, which the gateway has no use for. The
    keyword arguments are the test seams of `create_app`.
    """
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    init_sentry(settings)

    engine = _request_engine(settings)
    session_factory = create_session_factory(engine)
    rate_limit_engine = _rate_limit_engine(settings)
    rate_limit_session_factory = create_session_factory(rate_limit_engine)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        runtime = _start_gateway(
            application,
            settings,
            session_factory,
            rate_limit_session_factory,
            transport=gateway_transport,
            resolver=gateway_resolver,
        )
        yield
        await _stop_gateway(runtime)  # spans first, while the engine is open
        await engine.dispose()
        await rate_limit_engine.dispose()
        await shutdown_tracing(getattr(application.state, "tracer_provider", None))

    # No OpenAPI or docs pages: the api serves the one OpenAPI document, gateway routes included.
    app = FastAPI(
        title="Spanlight Gateway",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.rate_limit_engine = rate_limit_engine
    app.state.rate_limit_session_factory = rate_limit_session_factory
    app.state.clock = clock or utcnow

    install_error_handlers(app)  # `/health/*` and `/metrics` answer problem+json, as on the api
    install_gateway_error_handlers(app)
    # Its own service name (`spanlight-gateway`), so its spans are told apart from the api's.
    configure_tracing(settings, app, engine, role="gateway")
    app.add_middleware(RequestContextMiddleware)

    for router in (gw.router, health.router):
        app.include_router(router)
    return app


def _request_engine(settings: Settings) -> AsyncEngine:
    # A request waits only briefly for a connection: a pool that is full means the database is
    # behind, and an early 503 lets the client back off instead of piling up behind it.
    # No pre-ping: it is a `SELECT 1` round trip on every request. After a Postgres restart the
    # first request on each stale connection answers 503 `Retry-After` instead (`app.core.errors`).
    return create_engine(
        settings.database_url,
        pool_timeout=settings.api_pool_timeout_seconds,
        pre_ping=False,
        statement_timeout_ms=round(settings.api_statement_timeout_seconds * 1000),
    )


def _rate_limit_engine(settings: Settings) -> AsyncEngine:
    # Rate-limit checks run on their own connections, outside the request's transaction (see
    # `app.core.ratelimit`). A pool apart from the idempotency one, so a burst of requests being
    # limited cannot starve idempotency reservations, which fail the request when they time out.
    return create_engine(
        settings.database_url,
        pool_size=settings.rate_limit_pool_size,
        max_overflow=0,
        pool_timeout=settings.rate_limit_pool_timeout_seconds,
        # A stale connection fails one check, which fails open and is logged; a ping on every
        # checkout would add a round trip to every ingest and bearer read.
        pre_ping=False,
        # Each check is one atomic statement (`PostgresTokenBucket.acquire`), so a transaction
        # around it would only add the BEGIN and COMMIT round trips.
        isolation_level="AUTOCOMMIT",
    )


def _start_gateway(
    application: FastAPI,
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    rate_limit_sessions: async_sessionmaker[AsyncSession],
    *,
    transport: httpx.AsyncBaseTransport | None,
    resolver: Resolver | None,
) -> GatewayRuntime:
    """Build the upstream client and the gateway runtime, and keep both on `application.state`.

    The one client for all traffic to LLM providers (see `app.gateway.http`). Built per
    lifespan, so a second startup of the same app does not get the closed one. Also starts the
    flush of the gateway's span losses (`app.telemetry_losses.flush`), which `_stop_gateway` stops.
    """
    http = build_http_client(settings, transport=transport, resolver=resolver)
    runtime = build_runtime(settings, sessions, http=http, rate_limit_sessions=rate_limit_sessions)
    application.state.gateway_http = http
    application.state.gateway_runtime = runtime
    loss_flush.start(sessions)
    return runtime


async def _stop_gateway(runtime: GatewayRuntime) -> None:
    """Write the spans still queued, then the loss counts, the drain's own losses included.

    Both before the engines are disposed; the loss flush waits at most `SHUTDOWN_BUDGET_S`.
    """
    try:
        await close_runtime(runtime)
    finally:
        await loss_flush.stop(timeout_s=loss_flush.SHUTDOWN_BUDGET_S)


async def _sync_prices(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Make sure the bundled price snapshot is present. Never blocks startup."""
    try:
        async with session_factory() as session:
            inserted = await sync_seed_prices(session)
            await session.commit()
    except SQLAlchemyError as exc:
        logger.warning("price_sync_failed", error_type=type(exc).__name__)
        return
    if inserted:
        logger.info("price_sync_inserted", rows=inserted)


app = create_app()

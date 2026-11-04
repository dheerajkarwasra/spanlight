"""Self-tracing: the api and the worker export OpenTelemetry spans about themselves.

Off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set. With it set, the api traces its HTTP requests
(FastAPI), database queries (SQLAlchemy) and outbound calls (httpx); the worker traces the last two.
The endpoint can be any OTLP HTTP collector, including a Spanlight instance's own
`/v1/otlp/traces`, which is why ingestion, health and metrics requests are never traced: an
instance tracing into itself would otherwise create a span for every batch of spans it receives.

What is recorded is deliberately narrow:

- Request bodies and headers are not captured. The instrumentations only capture headers when
  asked to, and nothing here asks, but the FastAPI one also reads the
  `OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_*` environment variables (an empty list in code
  falls back to them). So `SanitizingSpanExporter` removes every `http.request.header.*` and
  `http.response.header.*` attribute, and setting those variables cannot put an `Authorization`
  or `Cookie` header on a span.
- URLs are recorded without their query string, which can hold bearer secrets (an invite
  `?token=`, an OAuth callback's `code` and `state`). The instrumentations put it on the spans
  themselves (`http.url`, `url.full`, `http.target`, `url.query`), so `SanitizingSpanExporter`
  removes it from every span just before export.
- Server spans carry no information about the client or the `Host` the client chose: client
  address and port, user agent, peer and host attributes are removed by the same exporter.
- Error text is replaced by the exception class name. SQLAlchemy puts the database error's
  message in the span status and FastAPI records exception messages, and a Postgres message can
  quote row values (`Key (email)=(a@b.com) already exists`). Exception stack traces are dropped.
- SQL spans carry the statement text, which holds bind placeholders and never the parameter
  values: the instrumentation does not read them, and sqlcommenter is left off.
- Request spans carry `spanlight.request_id` and `spanlight.principal_kind` (session, pat, api_key
  or anonymous), never an id, email or credential.
"""

import asyncio
import atexit
import contextlib
import threading
from collections.abc import Sequence
from urllib.parse import urlsplit

import structlog
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy.engine import EngineTracer
from opentelemetry.metrics import NoOpMeter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import SpanKind
from opentelemetry.trace.status import Status, StatusCode
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.deps import HOST_PREFIX, SESSION_COOKIE
from app.config import Settings
from app.core.request_context import current_request_id
from app.core.security import PAT_PREFIX, is_bearer

logger = structlog.get_logger(__name__)

# Matched against `scheme://host/path` (the instrumentation leaves the query out of the string it
# tests). Anchored at the path start, so `/v1/traces` and `/v1/otlp/traces` are excluded and
# `/api/v1/...` is not.
EXCLUDED_URLS = r"^[a-z]+://[^/]*/(v1|health|metrics)(/|$)"

_TRACES_PATH = "/v1/traces"
_OWN_OTLP_BASE = "/v1/otlp"
_SHUTDOWN_TIMEOUT_SECONDS = 5.0
_REQUEST_ID_ATTRIBUTE = "spanlight.request_id"
_PRINCIPAL_KIND_ATTRIBUTE = "spanlight.principal_kind"
_ROLE_ATTRIBUTE = "spanlight.role"


def configure_tracing(
    settings: Settings, app: FastAPI, engine: AsyncEngine, *, role: str = "api"
) -> None:
    """Instrument `app`, `engine` and httpx, and start exporting. A no-op without an endpoint.

    `role` names the process: `api`, or `gateway` for the standalone gateway, which reports as
    the api's service name with `-gateway` in place of a trailing `-api` (as the worker does).

    The side-channel engines (idempotency keys, rate limiting) are read from `app.state`, so their
    queries appear under the request that caused them. The provider is kept on
    `app.state.tracer_provider` for `shutdown_tracing`. It is passed to each instrumentation
    instead of being installed globally, so creating a second app in one process works.

    Call it before the app's own middleware is added: the attribute middleware below has to sit
    inside the one that assigns request ids. (The FastAPI instrumentation itself always wraps the
    whole stack, so its span covers everything.)
    """
    endpoint = settings.otel_exporter_otlp_endpoint
    if not endpoint:
        return

    service = settings.otel_service_name
    if role != "api":
        service = f"{service.removesuffix('-api')}-{role}"
    provider = _create_provider(endpoint, service, role=role)
    app.state.tracer_provider = provider

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        excluded_urls=EXCLUDED_URLS,
        # The per-message `receive` and `send` spans only add noise to every request.
        exclude_spans=["receive", "send"],
    )
    app.add_middleware(TraceAttributesMiddleware)

    engines = [engine]
    for name in ("idempotency_engine", "rate_limit_engine"):
        extra = getattr(app.state, name, None)
        if isinstance(extra, AsyncEngine):
            engines.append(extra)
    _instrument_engines(provider, engines)
    _instrument_httpx(provider)
    logger.info("tracing_enabled", service=service)


def configure_worker_tracing(settings: Settings, engine: AsyncEngine) -> TracerProvider | None:
    """Trace the worker's database queries and outbound calls. A no-op without an endpoint.

    The service name is the api's with `-worker` in place of a trailing `-api`, so the two
    processes are told apart in the collector. Returns the provider for `shutdown_tracing`.
    """
    endpoint = settings.otel_exporter_otlp_endpoint
    if not endpoint:
        return None
    name = settings.otel_service_name.removesuffix("-api")
    provider = _create_provider(endpoint, f"{name}-worker", role="worker")
    _instrument_engines(provider, [engine])
    _instrument_httpx(provider)
    logger.info("tracing_enabled", service=f"{name}-worker")
    return provider


async def shutdown_tracing(provider: TracerProvider | None) -> None:
    """Flush the spans still queued and stop exporting. Safe to call without a provider.

    Flushing talks to the collector, which may be down, so it runs on a daemon thread and is
    waited for at most five seconds: shutting the app down never hangs on it, and a flush
    still running when the process exits is abandoned (a worker thread from the default executor
    would be joined instead).
    """
    if provider is None:
        return
    # Import at teardown: gateway tracing imports this module during application construction.
    from app.process_shutdown import current  # noqa: PLC0415 - avoid that construction cycle

    process = current.get()
    loop = asyncio.get_running_loop()
    finished = asyncio.Event()
    failed = threading.Event()
    allowance = _SHUTDOWN_TIMEOUT_SECONDS
    if process is not None:
        allowance = min(allowance, max(0, process.deadline - loop.time()))
    # Keep the SDK fallback until ownership transfers here; never join this daemon twice.
    atexit.unregister(provider.shutdown)

    def flush_and_stop() -> None:
        try:
            provider.shutdown()
        except Exception:  # the exporter's failure must not stop the app shutting down
            failed.set()
            logger.warning("tracing_shutdown_failed", exc_info=True)
        finally:
            # The daemon exporter may finish after its owning event loop has closed.
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(finished.set)

    threading.Thread(target=flush_and_stop, name="tracing-shutdown", daemon=True).start()
    try:
        async with asyncio.timeout(allowance):
            await finished.wait()
    except TimeoutError:
        failed.set()
        logger.warning("tracing_shutdown_timed_out", timeout_seconds=allowance)
    if failed.is_set() and process is not None:
        process.incomplete(phase="resources", reason="close_failed")


def _create_provider(endpoint_base: str, service_name: str, *, role: str) -> TracerProvider:
    # The setting is a base URL, as in the OpenTelemetry environment variable of the same name; an
    # exporter given an endpoint explicitly would not add the signal path itself. A value that
    # already is the full traces URL is used as it is: anything ending in `/traces`, which covers
    # `/v1/traces` and Spanlight's own `/v1/otlp/traces`. Spanlight's OTLP base, `/v1/otlp`, gets
    # `/traces` rather than the standard `/v1/traces`, which would not exist under it.
    endpoint = endpoint_base.rstrip("/")
    if endpoint.endswith(_OWN_OTLP_BASE):
        endpoint += "/traces"
    elif not endpoint.endswith("/traces"):
        endpoint += _TRACES_PATH
    provider = TracerProvider(
        resource=Resource.create({SERVICE_NAME: service_name, _ROLE_ATTRIBUTE: role})
    )
    provider.add_span_processor(
        BatchSpanProcessor(SanitizingSpanExporter(OTLPSpanExporter(endpoint=endpoint)))
    )
    return provider


# Attributes of a server span that describe the client, or the Host header the client chose. The
# old and new HTTP semantic conventions both appear because either can be in force.
_CLIENT_ATTRIBUTES = frozenset(
    {
        "client.address",
        "client.port",
        "http.client_ip",
        "user_agent.original",
        "http.user_agent",
        "net.peer.ip",
        "net.peer.port",
        "net.peer.name",
        "net.sock.peer.addr",
        "net.sock.peer.port",
        "network.peer.address",
        "network.peer.port",
        "server.address",
        "server.port",
        "http.host",
        "http.server_name",
        "net.host.name",
        "net.host.port",
    }
)
_URL_ATTRIBUTES = ("http.url", "url.full")
_PATH_ATTRIBUTES = ("http.target", "url.path")
_EXCEPTION_TEXT_ATTRIBUTES = frozenset({"exception.message", "exception.stacktrace"})
# Captured header values, as the instrumentations name them (on any span kind).
_HEADER_ATTRIBUTE_PREFIXES = ("http.request.header.", "http.response.header.")


def _without_query(url: str) -> str | None:
    """`scheme://host/path` of a URL, or None if it does not parse (a crafted Host header)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    return parts._replace(query="", fragment="").geturl()


def _path_only(target: str) -> str:
    return target.partition("?")[0].partition("#")[0]


def _sanitized_attributes(span: ReadableSpan) -> dict[str, object]:
    attributes: dict[str, object] = dict(span.attributes or {})
    for name in _URL_ATTRIBUTES:
        value = attributes.get(name)
        if isinstance(value, str):
            cleaned = _without_query(value)
            if cleaned is None:
                del attributes[name]
            else:
                attributes[name] = cleaned
    for name in _PATH_ATTRIBUTES:
        value = attributes.get(name)
        if isinstance(value, str):
            attributes[name] = _path_only(value)
    attributes.pop("url.query", None)
    for name in [name for name in attributes if name.startswith(_HEADER_ATTRIBUTE_PREFIXES)]:
        del attributes[name]
    if span.kind == SpanKind.SERVER:
        for name in _CLIENT_ATTRIBUTES:
            attributes.pop(name, None)
    return attributes


def _sanitized_events(events: Sequence[Event]) -> list[Event]:
    sanitized: list[Event] = []
    for event in events:
        if event.name == "exception":
            kept = {
                key: value
                for key, value in (event.attributes or {}).items()
                if key not in _EXCEPTION_TEXT_ATTRIBUTES
            }
            event = Event(event.name, kept, event.timestamp)  # noqa: PLW2901
        sanitized.append(event)
    return sanitized


def _sanitized_status(span: ReadableSpan) -> Status:
    status = span.status
    if status.status_code is not StatusCode.ERROR:
        return status
    # The description is an exception's text. Keep only its class, which an exception event names.
    for event in span.events:
        exception_type = (event.attributes or {}).get("exception.type")
        if event.name == "exception" and isinstance(exception_type, str):
            return Status(StatusCode.ERROR, exception_type)
    return Status(StatusCode.ERROR)


def sanitize_span(span: ReadableSpan) -> ReadableSpan:
    """A copy of `span` that is safe to leave the process; see the module docstring."""
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=_sanitized_attributes(span),  # type: ignore[arg-type]
        events=_sanitized_events(span.events),
        links=span.links,
        kind=span.kind,
        instrumentation_scope=span.instrumentation_scope,
        status=_sanitized_status(span),
        start_time=span.start_time,
        end_time=span.end_time,
    )


class SanitizingSpanExporter(SpanExporter):
    """Rewrites each span with `sanitize_span`, then hands it to the exporter it wraps.

    Done at export time because the instrumentations set these attributes themselves, some of
    them when the span ends, and a finished span can no longer be changed.
    """

    def __init__(self, exporter: SpanExporter) -> None:
        self._exporter = exporter

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._exporter.export([sanitize_span(span) for span in spans])

    def shutdown(self) -> None:
        self._exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._exporter.force_flush(timeout_millis)


def _instrument_engines(provider: TracerProvider, engines: list[AsyncEngine]) -> None:
    """Add query spans to each engine.

    `EngineTracer` is what `SQLAlchemyInstrumentor.instrument(engine=...)` creates. It is used
    directly because the instrumentor is a process-wide singleton that also patches
    `create_engine` and only instruments once, which does not suit several apps per process.
    """

    tracer = provider.get_tracer("opentelemetry.instrumentation.sqlalchemy")
    # The tracer reports connection-pool usage through this counter. Metrics are not exported.
    connections_usage = NoOpMeter("spanlight").create_up_down_counter("db.client.connections.usage")
    for engine in engines:
        # Without `enable_commenter` nothing is added to the SQL, and span attributes hold the
        # statement text only.
        EngineTracer(  # type: ignore[no-untyped-call]
            tracer, engine.sync_engine, connections_usage, enable_commenter=False
        )


def _instrument_httpx(provider: TracerProvider) -> None:

    instrumentor = HTTPXClientInstrumentor()
    if instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.uninstrument()
    # No request or response hooks are given, so bodies and headers (Authorization) stay out.
    instrumentor.instrument(tracer_provider=provider)


def principal_kind(connection: HTTPConnection) -> str:
    """The kind of credential the request presents, from its shape alone.

    It is read before and without authenticating, so a request with a bad token is still counted
    as a token request. The credential itself is never recorded.
    """
    authorization = connection.headers.get("authorization")
    if is_bearer(authorization):
        credentials = (authorization or "").strip().partition(" ")[2].strip()
        return "pat" if credentials.startswith(PAT_PREFIX) else "api_key"
    if any(name in connection.cookies for name in (SESSION_COOKIE, HOST_PREFIX + SESSION_COOKIE)):
        return "session"
    return "anonymous"


class TraceAttributesMiddleware:
    """Puts the request id and the credential kind on the request's server span.

    It has to run inside `RequestContextMiddleware`, which assigns the request id, and inside the
    FastAPI instrumentation, whose span is the current one while it runs. It does nothing when
    there is no recording span (tracing off for the route, or sampled out).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            span = trace.get_current_span()
            if span.is_recording():
                request_id = current_request_id()
                if request_id is not None:
                    span.set_attribute(_REQUEST_ID_ATTRIBUTE, request_id)
                span.set_attribute(_PRINCIPAL_KIND_ATTRIBUTE, principal_kind(HTTPConnection(scope)))
        await self.app(scope, receive, send)

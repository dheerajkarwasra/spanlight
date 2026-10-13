"""The values one gateway call is made of: what was asked, who asks, and what came back.

Pure data. `GatewayRequest` is the client's call, `GatewayContext` everything the gateway knows
about the caller and the services it uses, `GatewayResult` what `execute` hands back to its
caller. The trace a call joins comes from `app.gateway.trace_headers`.

`key=None` on a context means an in-process caller (the demo job, later explanations and the
playground): no rate limits, cache or faults, and the environment comes from the context.
"""

import asyncio
import random
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import FaultProfile, ProviderCredential
from app.gateway.budget import BudgetDecision, BudgetGuard
from app.gateway.cache import CacheStatus, GatewayCache
from app.gateway.errors import GatewayError, Surface
from app.gateway.faults import AppliedFault
from app.gateway.key_context import KeyContext
from app.gateway.route_config import RouteConfig
from app.gateway.sse import Usage
from app.gateway.trace_headers import TraceHeaders

if TYPE_CHECKING:
    from app.config import Settings

Sleep = Callable[[float], Awaitable[None]]

UPSTREAM_ERROR_CODE = "UPSTREAM_ERROR"
"""`X-Spanlight-Code` on a passed-through provider error; also the code of its `GatewayError`."""

Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Received:
    """When the gateway took the call: `monotonic` (`time.monotonic`) and wall-clock `at` (UTC).

    The `/gw/v1` router takes it before it authenticates the key, so the overhead and time to
    first token include the gateway's own front half (authentication, limits, reading the body).
    `at` comes from `clock`, the app's wall clock, so a call's span starts on the same clock that
    the guard, the cache and the span's end read.
    """

    monotonic: float
    at: datetime

    @classmethod
    def now(cls, clock: Clock = _now) -> "Received":
        return cls(monotonic=time.monotonic(), at=clock())


@dataclass(frozen=True)
class GatewayRequest:
    """One call as the client made it. `body` is the parsed JSON object, `model` a string in it.

    `client_headers` are the request's headers; adapters copy only their allowlist upstream.
    `request_id` names the call in error bodies and the `X-Request-ID` header. `received` is
    when the call arrived; None (an in-process caller) starts the clocks when `execute` begins.
    """

    surface: Surface
    body: dict[str, Any]
    stream: bool
    trace: TraceHeaders = field(default_factory=TraceHeaders)
    client_headers: Mapping[str, str] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    received: Received | None = None


class ByteStream(Protocol):
    """A stream of response bytes the caller must either read to the end or `aclose()`."""

    def __aiter__(self) -> AsyncIterator[bytes]: ...

    async def __anext__(self) -> bytes: ...

    async def aclose(self) -> None: ...


class SpanSink(Protocol):
    """Takes a call's span instead of the background recorder, for a caller that writes its spans
    itself and must know each write landed (the demo job: an unrecorded paid call is spend its
    monthly cap never sees). `credential_ids` are the credentials the call used."""

    def add(self, span: dict[str, Any], credential_ids: Sequence[uuid.UUID]) -> None: ...


class GatewayServices(Protocol):
    """The process-wide services a context is built from (Task 15's runtime has them)."""

    @property
    def http(self) -> httpx.AsyncClient: ...

    @property
    def budget_guard(self) -> BudgetGuard: ...

    @property
    def cache(self) -> GatewayCache: ...

    @property
    def sessions(self) -> async_sessionmaker[AsyncSession]: ...

    @property
    def settings(self) -> "Settings": ...


def new_rng() -> random.Random:
    """A generator seeded from the operating system, for a call that is not under test."""
    return random.Random(secrets.randbits(64))  # noqa: S311 - routing and faults, not secrets


@dataclass(frozen=True)
class GatewayContext:
    """Who is calling and through which route, plus the services the call uses.

    `rng` drives the weighted target pick and the fault draw, so a seeded one makes a call
    deterministic. `clock` gives wall time for span timestamps and the database; `monotonic`
    and `sleep` measure and wait, and tests replace them.
    """

    key: KeyContext | None
    project_id: uuid.UUID
    org_id: uuid.UUID
    environment: str
    route: RouteConfig
    route_id: uuid.UUID
    route_name: str
    route_version: int
    credentials: Mapping[uuid.UUID, ProviderCredential]
    capture_payloads: bool
    budget_guard: BudgetGuard
    cache: GatewayCache
    http: httpx.AsyncClient
    sessions: async_sessionmaker[AsyncSession]
    settings: "Settings"
    rng: random.Random = field(default_factory=new_rng)
    clock: Clock = _now
    monotonic: Callable[[], float] = time.monotonic
    sleep: Sleep = asyncio.sleep
    span_sink: SpanSink | None = None  # None: spans go to the background recorder

    @property
    def key_id(self) -> uuid.UUID | None:
        return self.key.key.id if self.key is not None else None

    @property
    def allowed_models(self) -> list[str]:
        return list(self.key.key.allowed_models) if self.key is not None else []

    @property
    def default_tags(self) -> list[str]:
        return list(self.key.key.default_tags) if self.key is not None else []

    @property
    def cache_ttl_seconds(self) -> int | None:
        """The key's cache TTL; None (cache off) for a key without one or an in-process caller."""
        return self.key.key.cache_ttl_seconds if self.key is not None else None

    @property
    def fault_profile(self) -> FaultProfile | None:
        return self.key.fault_profile if self.key is not None else None


def context_for_key(
    key: KeyContext,
    services: GatewayServices,
    *,
    rng: random.Random | None = None,
    clock: Clock = _now,
) -> GatewayContext:
    """The context of a call made with a gateway key, after its secret and limits passed.

    `clock` is the wall clock of the call: the router passes the app's, which tests can pin.
    """
    return GatewayContext(
        key=key,
        project_id=key.project_id,
        org_id=key.org_id,
        environment=key.key.environment,
        route=key.route,
        route_id=key.route_id,
        route_name=key.route_name,
        route_version=key.route_version,
        credentials=key.credentials,
        capture_payloads=key.capture_payloads,
        budget_guard=services.budget_guard,
        cache=services.cache,
        http=services.http,
        sessions=services.sessions,
        settings=services.settings,
        rng=rng if rng is not None else new_rng(),
        clock=clock,
    )


@dataclass(frozen=True)
class Attempt:
    """One upstream attempt. `status` is None when no response came (`error` says why)."""

    target_index: int
    credential_id: uuid.UUID
    status: int | None
    error: str | None
    duration_ms: float
    retry_after: float | None = None


@dataclass
class GatewayResult:
    """What `execute` answers with.

    Exactly one of `body` and `stream` is set. `body` is the response as bytes (an upstream
    answer passed through, a cached answer or a rendered gateway error); `stream` yields the
    upstream bytes unchanged and writes the span when it ends, or when the client goes away.
    A caller that does not read `stream` to the end must `aclose()` it (in a `finally`), which
    closes the provider connection and writes the span even when no byte was read.
    `error` is set for every answer that is not a provider success: a gateway error (also
    rendered into `body`) or, for an upstream error, a `GatewayError` describing it for the span.
    """

    status: int
    headers: dict[str, str]
    body: bytes | None
    stream: ByteStream | None
    trace_id: str
    span_id: str
    model: str | None = None
    usage: Usage | None = None
    ttft_ms: float | None = None
    attempts: list[Attempt] = field(default_factory=list)
    target_index: int | None = None
    cache: CacheStatus = "off"
    fault: AppliedFault | None = None
    error: GatewayError | None = None
    finish_reason: str | None = None
    output: dict[str, Any] | None = None
    budget: BudgetDecision | None = None  # set when a budget blocked the call
    original_usage: Usage | None = None  # a cache hit's stored usage
    overhead_ms: float | None = None
    client_disconnected: bool = False
    disconnected_after_ms: float | None = None
    stream_error: str | None = None  # why a stream ended before the provider finished it
    cut_by_fault: bool = False  # a `truncated_stream` fault withheld the end of the stream

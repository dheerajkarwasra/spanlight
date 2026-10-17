"""Run a prompt against up to three configurations at once, each through the gateway in process.

`run` has three steps, and no transaction is open while the providers answer:

1. **Prepare**, in the request's project-bound transaction: every configuration's credential
   (an organization credential) and prompt version (a version of one of the project's prompts)
   are loaded, the prompt is compiled and the request built, and the price book is read. Then
   the transaction is committed, which returns its connection to the pool. A credential or
   version that is not found fails the whole request with `404`; a prompt with a missing
   variable fails only its own configuration.
2. **Call**, concurrently, through a route built in memory (`single_credential_context`): the
   configuration's credential and model, one attempt, no fallbacks. The trace's environment is
   `playground`, its name `playground`, its tag `spanlight:playground` and its user the caller;
   the span records the prompt version when one was used. The span is written into the project
   like any other call (it is real spend there) and awaited (`DirectSpans`), shielded from
   cancellation so a client that goes away mid-call still leaves its span. The project's `block`
   budgets apply (`error = "BUDGET_EXCEEDED"`); the playground has no cap of its own, since a
   person is typing and each call is bounded by `max_tokens`.
3. **Answer** with one result per configuration, in order. One failing never affects the others.

Prompts and answers are never logged.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_configured, not_found
from app.core.ratelimit import PostgresTokenBucket
from app.db.models import Project, PromptVersion, ProviderCredential
from app.gateway import service as gateway_service
from app.gateway.context import (
    Clock,
    GatewayContext,
    GatewayRequest,
    GatewayResult,
    GatewayServices,
)
from app.gateway.execute import execute as gateway_execute
from app.gateway.in_process import single_credential_context
from app.gateway.recorder import DirectSpans
from app.gateway.settle_helpers import shielded
from app.gateway.trace_headers import TraceHeaders
from app.playground import request as build
from app.playground.output import failed, result_of
from app.playground.schemas import MessagesSource, PlaygroundConfig, PlaygroundResult
from app.pricing.cost import PriceBook, load_price_book
from app.prompts import service as prompt_service
from app.prompts.templates import Message, MissingVariablesError, TemplateTooLargeError

logger = structlog.get_logger(__name__)

ENVIRONMENT = "playground"
TRACE_NAME = "playground"
TAG = "spanlight:playground"

PLAYGROUND_LIMITER = PostgresTokenBucket(rate=30 / 60, burst=30)
"""Per user: 30 runs a minute (a run is one request of up to three configurations)."""

Execute = Callable[[GatewayRequest, GatewayContext], Awaitable[GatewayResult]]


@dataclass(frozen=True)
class _Plan:
    """One configuration, prepared: what to send with which credential, or why it cannot be."""

    credential: ProviderCredential
    model: str
    version_id: uuid.UUID | None
    request: GatewayRequest | None
    error: str | None


class _PlaygroundSpans(DirectSpans):
    """Takes a call's span, naming its trace and tying it to the prompt version, if any."""

    def __init__(self, version_id: uuid.UUID | None) -> None:
        super().__init__()
        self._version_id = version_id

    def add(self, span: dict[str, Any], credential_ids: Any) -> None:
        trace = span.get("trace") or {}
        span["trace"] = {**trace, "name": TRACE_NAME}
        if self._version_id is not None:
            span["prompt_version_id"] = str(self._version_id)
        super().add(span, credential_ids)


async def run(
    db: AsyncSession,
    services: GatewayServices | None,
    project: Project,
    actor_id: uuid.UUID,
    configs: list[PlaygroundConfig],
    *,
    clock: Clock,
    execute: Execute = gateway_execute,
) -> list[PlaygroundResult]:
    """Run every configuration and answer in order. Commits `db`.

    Errors: `409 NOT_CONFIGURED` without the gateway runtime or `CREDENTIALS_KEYS`; `404` for a
    credential that is not the organization's or a prompt version that is not the project's.
    `clock` is the app's wall clock: the budget guard, the spans and the pricing all read it.
    """
    services = require_services(services)
    plans = [await _plan(db, project, actor_id, config) for config in configs]
    prices = await load_price_book(db, project.org_id)
    await db.commit()  # no connection is held while the providers answer
    results = await asyncio.gather(
        *(_run_one(services, project, plan, prices, clock, execute) for plan in plans)
    )
    logger.info(
        "playground_run",
        project_id=str(project.id),
        configs=len(plans),
        failed=sum(1 for result in results if result.error is not None),
    )
    return list(results)


def require_services(services: GatewayServices | None) -> GatewayServices:
    """The gateway services, when the runtime runs here and credentials can be read; else
    `409 NOT_CONFIGURED`. Shared with the replay service."""
    if services is None:
        raise not_configured("The gateway runtime is not running in this process.")
    if not services.settings.is_crypto_configured:
        raise not_configured("Set CREDENTIALS_KEYS so provider credentials can be used.")
    return services


async def _plan(
    db: AsyncSession, project: Project, actor_id: uuid.UUID, config: PlaygroundConfig
) -> _Plan:
    credential = await gateway_service.org_credential(db, project.org_id, config.credential_id)
    if credential is None:
        raise not_found()
    source = config.source
    version: PromptVersion | None = None
    if isinstance(source, MessagesSource):
        messages = [Message(m.role, m.content) for m in source.messages]
    else:
        version = await prompt_service.project_version(db, project.id, source.prompt_version_id)
        if version is None:
            raise not_found()
        try:
            messages = build.compile_version(version, source.variables)
        except MissingVariablesError as error:
            return _Plan(
                credential, config.model, version.id, None, build.missing_variables_error(error)
            )
        except TemplateTooLargeError:
            return _Plan(credential, config.model, version.id, None, build.PROMPT_TOO_LARGE)
    body = build.build_body(credential.provider, config.model, config.params, messages)
    request = GatewayRequest(
        surface=build.surface_for(credential.provider),
        body=body,
        stream=False,
        trace=TraceHeaders(user_id=str(actor_id), tags=[TAG]),
    )
    return _Plan(credential, config.model, version.id if version else None, request, None)


async def _run_one(
    services: GatewayServices,
    project: Project,
    plan: _Plan,
    prices: PriceBook,
    clock: Clock,
    execute: Execute,
) -> PlaygroundResult:
    """One configuration's result; anything but a cancellation becomes its `error`."""
    if plan.request is None:
        return failed(plan.error or "invalid_configuration")
    try:
        return await _call(services, project, plan, plan.request, prices, clock, execute)
    except Exception as error:  # noqa: BLE001 - one broken configuration must not stop the others
        logger.error("playground_call_failed", error_type=type(error).__name__)
        return failed("playground_failed")


async def _call(
    services: GatewayServices,
    project: Project,
    plan: _Plan,
    request: GatewayRequest,
    prices: PriceBook,
    clock: Clock,
    execute: Execute,
) -> PlaygroundResult:
    spans = _PlaygroundSpans(plan.version_id)
    context = single_credential_context(
        services,
        project,
        plan.credential,
        environment=ENVIRONMENT,
        route_name=TRACE_NAME,
        span_sink=spans,
        clock=clock,
    )
    started = time.monotonic()
    try:
        result = await execute(request, context)
    except BaseException:
        # `execute` raises only when cancelled (the client went away); the call may have been paid.
        await shielded(spans.write(context))
        raise
    latency_ms = (time.monotonic() - started) * 1000
    # An answered call always leaves a span; none means it could not be built.
    stored = await shielded(spans.write(context, require_span=True))
    outcome = result_of(
        result,
        surface=request.surface,
        provider=plan.credential.provider.value,
        model=plan.model,
        prices=prices,
        latency_ms=latency_ms,
        at=clock(),
    )
    return _linked(outcome, stored=stored, project_id=project.id)


def _linked(outcome: PlaygroundResult, *, stored: bool, project_id: uuid.UUID) -> PlaygroundResult:
    """The outcome, without its trace link when its span was not stored.

    A trace link that cannot open is worse than none.
    """
    if stored:
        return outcome
    logger.warning("playground_span_not_stored", project_id=str(project_id))
    return replace(outcome, trace_id=None, span_id=None)

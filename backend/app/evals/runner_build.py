"""What a run needs before it evaluates: its dependencies, its input and its evaluator.

`build_eval_input` pairs the target span's stored (already redacted) input and output and adds
the trace and span fields a webhook sends. `build_evaluator` turns a stored evaluator into the
object that scores: a heuristic rule, a judge on its credential, prompt version and price book,
or a webhook on its alert channel with the secret opened. A misconfiguration raises
`EvaluatorError` with a stable code, and the run fails with it without calling anybody.

A score is named after its evaluator: evaluator names follow the score name rule
(`app.evals.config.EvaluatorName`), so the name is used as it is.
"""

from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.channels.http import build_alert_client
from app.alerts.channels.resolve import resolve_channel
from app.db.models import AlertChannelKind, Evaluator, Project, Span, Trace
from app.evals.config import (
    HeuristicEvaluatorConfig,
    JudgeConfig,
    WebhookEvaluatorConfig,
    parse_config,
)
from app.evals.evaluators.base import EvaluatorError
from app.evals.evaluators.heuristic import HeuristicEvaluator
from app.evals.evaluators.judge import Execute, JudgeEvaluator
from app.evals.evaluators.webhook import WebhookEvaluator
from app.evals.schemas import EvalInput
from app.gateway import service as gateway_service
from app.gateway.context import GatewayServices
from app.gateway.execute import execute as gateway_execute
from app.notifications.registry import PermanentDeliveryError
from app.pricing.cost import load_price_book
from app.prompts import service as prompt_service

if TYPE_CHECKING:
    from app.config import Settings

AnyEvaluator = HeuristicEvaluator | JudgeEvaluator | WebhookEvaluator


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class RunnerDeps:
    """What every run uses. `services` is the gateway runtime, required by judges only;
    `heartbeat` extends the worker's job lease before a paid call."""

    sessions: async_sessionmaker[AsyncSession]
    settings: "Settings"
    services: GatewayServices | None = None
    execute: Execute = gateway_execute
    clock: Callable[[], datetime] = _utcnow
    heartbeat: Callable[[], Awaitable[None]] | None = None


def trace_fields(trace: Trace) -> dict[str, Any]:
    return {
        "trace_id": trace.trace_id,
        "name": trace.name,
        "environment": trace.environment,
        "release": trace.release,
        "user_id": trace.external_user_id,
        "session_id": trace.session_id,
        "tags": list(trace.tags or ()),
    }


def build_eval_input(
    trace: Trace, span: Span, *, run_id: Any, expected_output: Any | None = None
) -> EvalInput:
    """The span's stored input and output, with the run, trace and span fields in `metadata`."""
    return EvalInput(
        trace_id=trace.trace_id,
        span_id=span.span_id,
        input=span.input,
        output=span.output,
        expected_output=expected_output,
        metadata={
            "run_id": str(run_id),
            "trace": trace_fields(trace),
            "span": {"span_id": span.span_id, "name": span.name, "model": span.model},
        },
    )


async def build_evaluator(
    db: AsyncSession,
    deps: RunnerDeps,
    project: Project,
    evaluator: Evaluator,
    stack: AsyncExitStack,
) -> AnyEvaluator:
    """The scorer for `evaluator` in `project`. `db` is bound to the project; resources the
    scorer opens (a webhook's HTTP client) close with `stack`. Raises `EvaluatorError`."""
    try:
        config = parse_config(evaluator.config)
    except ValidationError:
        raise EvaluatorError("evaluator_config_invalid") from None
    name = evaluator.name
    if isinstance(config, HeuristicEvaluatorConfig):
        return HeuristicEvaluator(config.check, name)
    if isinstance(config, JudgeConfig):
        return await _judge(db, deps, project, config, name)
    return await _webhook(deps, project, evaluator, config, stack)


async def _judge(
    db: AsyncSession, deps: RunnerDeps, project: Project, config: JudgeConfig, name: str
) -> JudgeEvaluator:
    services = deps.services
    if services is None:
        raise EvaluatorError("gateway_not_configured")
    if not deps.settings.is_crypto_configured:
        raise EvaluatorError("credentials_not_configured")
    version = await prompt_service.project_version(db, project.id, config.prompt_version_id)
    if version is None:
        raise EvaluatorError("judge_prompt_missing")
    credential = await gateway_service.org_credential(db, project.org_id, config.credential_id)
    if credential is None:
        raise EvaluatorError("judge_credential_missing")
    return JudgeEvaluator(
        config,
        name,
        project=project,
        credential=credential,
        version=version,
        prices=await load_price_book(db, project.org_id),
        services=services,
        execute=deps.execute,
        clock=deps.clock,
    )


async def _webhook(
    deps: RunnerDeps,
    project: Project,
    evaluator: Evaluator,
    config: WebhookEvaluatorConfig,
    stack: AsyncExitStack,
) -> WebhookEvaluator:
    try:
        channel = await resolve_channel(deps.sessions, config.channel_id, settings=deps.settings)
    except PermanentDeliveryError:
        raise EvaluatorError("webhook_secret_unreadable") from None
    if channel is None or channel.org_id != project.org_id:
        raise EvaluatorError("webhook_channel_missing")
    if channel.kind != AlertChannelKind.WEBHOOK:
        raise EvaluatorError("webhook_channel_kind")
    client = build_alert_client(allow_private=deps.settings.webhook_allow_private_targets)
    stack.push_async_callback(client.aclose)
    return WebhookEvaluator(
        config, evaluator.name, channel, client=client, evaluator_id=evaluator.id
    )

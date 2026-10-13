"""Score a trace or span by asking a model: the LLM judge.

A judge compiles its prompt version with the evaluation's `input`, `output` and `expected_output`
(`app.evals.judge_templates.judge_variables`) and sends one call through the gateway in process,
on a route built in memory with the configured credential and nothing else (one attempt, no
fallbacks): OpenAI gets `chat_completions` with the system message first, Anthropic `messages`
with the system message in `system`. The call's trace has environment `evals`, name
`judge:<evaluator name>` and tag `spanlight:judge` (the sampler never samples it); its span is
written into the project like any other call and awaited before the judge returns.

The run splits in two so the runner can reserve the budget in between (ADR 0015): `prepare` is
pure and gives the request and its cost estimate; `call` sends it and reads the verdict
(`app.evals.verdict`). The verdict's score is the score, its reasoning the comment; the cost is
the price book's cost of the reported usage, or the estimate when the usage is missing or
unpriced. A failed call raises `JudgeCallError` with what it should be charged
(`app.evals.budget.failed_call_charge`). `evaluate` does both steps with no reservation, for a
caller that has already reserved. Prompts, answers and reasoning are never logged.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog

from app.db.models import Project, PromptVersion, ProviderCredential, ProviderKind
from app.evals.budget import estimate_call_cost, failed_call_charge
from app.evals.config import JudgeConfig
from app.evals.evaluators.base import EvaluatorError
from app.evals.judge_templates import judge_variables
from app.evals.schemas import EvalInput, EvalResult
from app.evals.verdict import VerdictUnparseable, parse_verdict
from app.gateway.context import GatewayContext, GatewayRequest, GatewayResult, GatewayServices
from app.gateway.errors import Surface
from app.gateway.execute import execute as gateway_execute
from app.gateway.in_process import single_credential_context
from app.gateway.recorder import DirectSpans
from app.gateway.response_summary import summarize_response
from app.gateway.settle_helpers import may_have_billed
from app.gateway.trace_headers import TraceHeaders
from app.pricing.cost import PriceBook
from app.prompts.templates import (
    InvalidTemplateError,
    Message,
    MissingVariablesError,
    Template,
    TemplateTooLargeError,
    compile,
)
from app.scores.schemas import ScoreIn

ENVIRONMENT = "evals"
JUDGE_TAG = "spanlight:judge"
ROUTE_NAME = "evals-judge"
TRACE_NAME_PREFIX = "judge:"
# The call's whole budget. A sampled run executes in a worker task that is cancelled after 50 s;
# ending the call well before that lets the gateway record the timeout and the run settle.
CALL_TIMEOUT_MS = 30_000

Execute = Callable[[GatewayRequest, GatewayContext], Awaitable[GatewayResult]]

logger = structlog.get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class JudgeCallError(EvaluatorError):
    """A judge call that produced no verdict. `charge` is what the run should be charged."""

    def __init__(self, code: str, charge: Decimal) -> None:
        super().__init__(code)
        self.charge = charge


@dataclass(frozen=True, slots=True)
class JudgeCall:
    """One prepared judge call: the request, its compiled prompt's size and its cost ceiling."""

    input: EvalInput
    request: GatewayRequest
    input_chars: int
    estimate: Decimal


class _NamedTraceSpans(DirectSpans):
    """Direct span writes that also name the trace."""

    def __init__(self, trace_name: str) -> None:
        super().__init__()
        self._trace_name = trace_name

    def add(self, span: dict[str, Any], credential_ids: Sequence[Any]) -> None:
        trace = {**(span.get("trace") or {}), "name": self._trace_name}
        super().add({**span, "trace": trace}, credential_ids)


class JudgeEvaluator:
    """Emits one score named after the evaluator, from one model call."""

    def __init__(
        self,
        config: JudgeConfig,
        name: str,
        *,
        project: Project,
        credential: ProviderCredential,
        version: PromptVersion,
        prices: PriceBook,
        services: GatewayServices,
        execute: Execute = gateway_execute,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.config = config
        self.name = name
        self._project = project
        self._credential = credential
        self._version = version
        self._prices = prices
        self._services = services
        self._execute = execute
        self._clock = clock

    async def evaluate(self, input: EvalInput) -> EvalResult:
        return await self.call(self.prepare(input))

    def prepare(self, input: EvalInput) -> JudgeCall:
        """The call for `input` and its estimate. Raises `EvaluatorError` with
        `judge_template_invalid`, `judge_prompt_too_large`, `judge_provider_unsupported` or
        `evaluator_model_unpriced`."""
        messages = _compile(self._version, judge_variables(input))
        provider = self._credential.provider
        if provider not in (ProviderKind.OPENAI, ProviderKind.ANTHROPIC):
            raise EvaluatorError("judge_provider_unsupported")
        input_chars = sum(len(message.content) for message in messages)
        estimate = estimate_call_cost(
            self.config.model,
            "".join(message.content for message in messages),
            self.config.max_tokens,
            self._prices,
            provider=provider.value,
            at=self._clock(),
        )
        # A zero price (an organization's override) would make every call free to the budget.
        if estimate is None or estimate <= 0:
            raise EvaluatorError("evaluator_model_unpriced")
        request = GatewayRequest(
            surface=_surface(provider),
            body=_body(provider, self.config, messages),
            stream=False,
            trace=TraceHeaders(tags=[JUDGE_TAG]),
        )
        return JudgeCall(input, request, input_chars, estimate)

    async def call(self, prepared: JudgeCall) -> EvalResult:
        """Send the call and read the verdict. Raises `JudgeCallError`.

        A cancellation propagates after the span is written; the runner charges the estimate.
        """
        spans = _NamedTraceSpans(f"{TRACE_NAME_PREFIX}{self.name}")
        context = single_credential_context(
            self._services,
            self._project,
            self._credential,
            environment=ENVIRONMENT,
            route_name=ROUTE_NAME,
            span_sink=spans,
            timeout_ms=CALL_TIMEOUT_MS,
            clock=self._clock,
        )
        try:
            result = await self._execute(prepared.request, context)
        finally:
            await asyncio.shield(self._write_spans(spans, context))
        return self._read(prepared, result)

    def _read(self, prepared: JudgeCall, result: GatewayResult) -> EvalResult:
        if result.budget is not None:  # a project `block` budget stopped it before sending
            raise JudgeCallError("gateway_budget_blocked", Decimal(0))
        usage_cost = self._usage_cost(prepared, result)
        text = None
        if result.error is None and result.status == 200 and result.body is not None:
            text = _answer_text(prepared.request.surface, result.body)
        if text is None:
            billed = may_have_billed(result)
            charge = failed_call_charge(
                may_have_billed=billed, usage_cost=usage_cost, estimate=prepared.estimate
            )
            raise JudgeCallError(_failure_code(result), charge)
        try:
            verdict = parse_verdict(text)
        except VerdictUnparseable as error:
            charge = failed_call_charge(
                may_have_billed=True, usage_cost=usage_cost, estimate=prepared.estimate
            )
            raise JudgeCallError(error.code, charge) from None
        return EvalResult(
            scores=[_score(prepared.input, self.name, verdict.score, verdict.reasoning)],
            cost_usd=prepared.estimate if usage_cost is None else usage_cost,
            reasoning=verdict.reasoning,
        )

    def _usage_cost(self, prepared: JudgeCall, result: GatewayResult) -> Decimal | None:
        """The price book's cost of the call's reported usage; None when unknown."""
        usage = result.usage
        if usage is None:
            return None
        priced = self._prices.cost(
            provider=self._credential.provider.value,
            model=result.model or self.config.model,
            at=self._clock(),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_tokens=usage.cached_tokens,
        )
        return None if priced is None else priced.cost_usd

    async def _write_spans(self, spans: DirectSpans, context: GatewayContext) -> None:
        if not await spans.write(context):
            logger.warning("evals_judge_span_unrecorded", project_id=str(self._project.id))


def _compile(version: PromptVersion, variables: dict[str, str]) -> list[Message]:
    stored = version.template
    if not isinstance(stored, dict) or stored.get("kind") != "chat":
        raise EvaluatorError("judge_template_invalid")
    try:
        template = Template(
            "chat",
            messages=[Message(m["role"], m["content"]) for m in stored.get("messages", [])],
        )
        compiled = compile(template, variables)
    except TemplateTooLargeError:
        raise EvaluatorError("judge_prompt_too_large") from None
    except (InvalidTemplateError, MissingVariablesError, KeyError, TypeError):
        raise EvaluatorError("judge_template_invalid") from None
    return compiled.messages or []


def _surface(provider: ProviderKind) -> Surface:
    return "messages" if provider == ProviderKind.ANTHROPIC else "chat_completions"


def _body(provider: ProviderKind, config: JudgeConfig, messages: list[Message]) -> dict[str, Any]:
    """The provider-native body: Anthropic takes the system text apart, OpenAI first.

    `temperature` is sent only when the config sets one: the newest Claude models reject
    non-default sampling parameters.
    """
    system = [m.content for m in messages if m.role == "system"]
    rest = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
    body: dict[str, Any] = {"model": config.model}
    if config.temperature is not None:
        body["temperature"] = float(config.temperature)
    if provider == ProviderKind.ANTHROPIC:
        body["max_tokens"] = config.max_tokens
        if system:
            body["system"] = "\n\n".join(system)
        body["messages"] = rest
    else:
        # OpenAI's newer models reject `max_tokens`.
        body["max_completion_tokens"] = config.max_tokens
        body["messages"] = [{"role": "system", "content": text} for text in system] + rest
    return body


def _answer_text(surface: Surface, body: bytes) -> str | None:
    """The text of a provider answer on `surface`; None when it has none."""
    output = summarize_response(surface, body).output
    content = output.get("content") if output is not None else None
    if isinstance(content, list):
        content = "".join(
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
    if not isinstance(content, str):
        return None
    return content.strip() or None


def _failure_code(result: GatewayResult) -> str:
    """`judge_status_<code>` for an error status, else `judge_no_answer` (a success without
    readable text, or a call that ended without a response)."""
    if result.status >= 400:
        return f"judge_status_{result.status}"
    return "judge_no_answer"


def _score(input: EvalInput, name: str, value: Decimal, reasoning: str) -> ScoreIn:
    return ScoreIn(
        trace_id=input.trace_id,
        span_id=input.span_id,
        name=name,
        value=value,
        comment=reasoning or None,
    )

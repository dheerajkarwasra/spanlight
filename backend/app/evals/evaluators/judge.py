"""Score a trace or span by asking a model: the LLM judge.

A judge compiles its prompt version with the evaluation's `input`, `output` and `expected_output`
(`app.evals.judge_templates.judge_variables`) and sends one call through the gateway in process,
on a route built in memory with the configured credential and nothing else (one attempt, no
fallbacks): OpenAI gets `chat_completions` with the system message first, Anthropic `messages`
with the system message in `system`. Both get one tool, `record_verdict`, and a closing system
sentence that overrides any stored "reply with JSON": OpenAI is forced to call it; Anthropic gets
`auto`, since its newest models reject a forced tool. The call's trace has environment `evals`, name
`judge:<evaluator name>` and tag `spanlight:judge` (the sampler never samples it); its span is
written into the project like any other call and awaited before the judge returns.

The run splits in two so the runner can reserve the budget in between (ADR 0015): `prepare` is
pure and gives the request and its cost estimate (the tool definition counts as input); `call`
sends it and reads the verdict from the tool call's arguments only (`app.evals.verdict`): an
answer without the call fails `verdict_unparseable`, whatever its text says. The verdict's score
is the score, its reasoning the comment; the cost is the price book's cost of the reported usage,
or the estimate when the usage is missing or unpriced. A failed call raises `JudgeCallError` with
what it should be charged (`app.evals.budget.failed_call_charge`). `evaluate` does both steps with
no reservation, for a caller that has already reserved. Prompts, answers and reasoning are never
logged.
"""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
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
from app.evals.verdict import VERDICT_TOOL_NAME, VerdictUnparseable, parse_verdict_arguments
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
# Claude models that think by default (generation 5 on, Fable and Mythos). Unasked, they think at
# medium effort or more, which can spend the whole `max_tokens` before the answer. Older models
# either do not think unasked or reject `effort` (Haiku 4.5, Sonnet 4.5).
_THINKS_BY_DEFAULT = re.compile(
    r"claude-(?:fable|mythos)-|claude-(?:opus|sonnet|haiku)-(?:[5-9]|\d\d)"
)
# Finish reasons of an answer cut off by its token limit: Anthropic's, then OpenAI's.
_TRUNCATED = ("max_tokens", "length")
_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {"score": {"type": "number"}, "reasoning": {"type": "string"}},
    "required": ["score", "reasoning"],
}
_VERDICT_DESCRIPTION = (
    "Record the verdict: a score from 0 to 1 as the instructions define it, and one or two "
    "sentences of reasoning."
)
# Appended last to every judge prompt, so it overrides a stored prompt's "reply with JSON".
_USE_TOOL = (
    f"Do not write the verdict as text; record it only by calling the {VERDICT_TOOL_NAME} tool."
)

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
        body = _body(provider, self.config, messages)
        estimate = estimate_call_cost(
            self.config.model,
            _prompt_text(body),
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
            body=body,
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
        if result.error is not None or result.status != 200 or result.body is None:
            billed = may_have_billed(result)
            charge = failed_call_charge(
                may_have_billed=billed, usage_cost=usage_cost, estimate=prepared.estimate
            )
            raise JudgeCallError(_failure_code(result), charge)
        summary = summarize_response(prepared.request.surface, result.body)
        truncated = summary.finish_reason in _TRUNCATED
        try:
            arguments = _verdict_arguments(summary.output)
            if arguments is None:
                raise VerdictUnparseable
            verdict = parse_verdict_arguments(arguments)
        except VerdictUnparseable as error:
            charge = failed_call_charge(
                may_have_billed=True, usage_cost=usage_cost, estimate=prepared.estimate
            )
            raise JudgeCallError("judge_truncated" if truncated else error.code, charge) from None
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
            cache_write_tokens=usage.cache_write_tokens,
            cache_write_1h_tokens=usage.cache_write_1h_tokens,
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
    non-default sampling parameters. A Claude model that thinks by default gets `low` effort: a
    verdict needs little reasoning, and the thinking shares `max_tokens` with the answer.
    """
    system = [m.content for m in messages if m.role == "system"] + [_USE_TOOL]
    rest = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
    body: dict[str, Any] = {"model": config.model}
    if config.temperature is not None:
        body["temperature"] = float(config.temperature)
    if provider == ProviderKind.ANTHROPIC:
        body["max_tokens"] = config.max_tokens
        if _THINKS_BY_DEFAULT.match(config.model):
            body["output_config"] = {"effort": "low"}
        body["system"] = "\n\n".join(system)
        body["messages"] = rest
        # Claude Opus 5.5, Sonnet 5.5 and Fable 5.1 reject a forced `tool_choice` with a 400.
        tool = {
            "name": VERDICT_TOOL_NAME,
            "description": _VERDICT_DESCRIPTION,
            "input_schema": _VERDICT_SCHEMA,
        }
        body["tools"] = [tool]
        body["tool_choice"] = {"type": "auto"}
    else:
        # OpenAI's newer models reject `max_tokens`.
        body["max_completion_tokens"] = config.max_tokens
        body["messages"] = [{"role": "system", "content": text} for text in system] + rest
        function = {
            "name": VERDICT_TOOL_NAME,
            "description": _VERDICT_DESCRIPTION,
            "parameters": _VERDICT_SCHEMA,
        }
        body["tools"] = [{"type": "function", "function": function}]
        body["tool_choice"] = {"type": "function", "function": {"name": VERDICT_TOOL_NAME}}
    return body


def _prompt_text(body: dict[str, Any]) -> str:
    """What the estimate counts as input: the system text, the messages and the tool definition."""
    messages = "".join(message["content"] for message in body["messages"])
    system: str = body.get("system", "")
    return system + messages + json.dumps(body["tools"])


def _verdict_arguments(output: dict[str, Any] | None) -> Mapping[str, Any] | str | None:
    """The arguments of the answer's `record_verdict` call: OpenAI's JSON string or Anthropic's
    input object. None when the answer made no such call; its text is never read."""
    if output is None:
        return None
    calls = output.get("tool_calls")
    for call in calls if isinstance(calls, list) else []:
        function = call.get("function") if isinstance(call, dict) else None
        if isinstance(function, dict) and function.get("name") == VERDICT_TOOL_NAME:
            arguments = function.get("arguments")
            return arguments if isinstance(arguments, str) else None
    content = output.get("content")
    for block in content if isinstance(content, list) else []:
        if (
            isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("name") == VERDICT_TOOL_NAME
        ):
            arguments = block.get("input")
            return arguments if isinstance(arguments, Mapping) else None
    return None


def _failure_code(result: GatewayResult) -> str:
    """`judge_status_<code>` for an error status, else `judge_no_answer` (a call that ended
    without a response). A response without a usable verdict call is `verdict_unparseable`, or
    `judge_truncated` when cut off by `max_tokens`, decided by the caller."""
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

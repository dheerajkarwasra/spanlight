"""Score a trace or span by asking a model: the LLM judge.

A judge compiles its prompt version with the evaluation's `input`, `output` and `expected_output`
(`app.evals.judge_templates.judge_variables`) and sends one call through the gateway in process,
on a route built in memory with the configured credential and nothing else (one attempt, no
fallbacks). Each provider gets its native request, one `record_verdict` tool and a closing system
instruction to use it. OpenAI and Gemini are forced to call it. Anthropic gets `auto` because newer
models reject a forced tool. The call's trace has environment `evals`, name
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

from app.budgets.gemini import reserve_gemini
from app.db.models import Project, PromptVersion, ProviderCredential, ProviderKind
from app.evals.budget import estimate_call_cost, failed_call_charge
from app.evals.config import JudgeConfig
from app.evals.evaluators.base import EvaluatorError
from app.evals.judge_templates import judge_variables
from app.evals.schemas import EvalInput, EvalResult
from app.evals.verdict import VERDICT_TOOL_NAME, VerdictUnparseable, parse_verdict_arguments
from app.gateway.context import GatewayContext, GatewayRequest, GatewayResult, GatewayServices
from app.gateway.execute import execute as gateway_execute
from app.gateway.in_process import single_credential_context
from app.gateway.recorder import DirectSpans
from app.gateway.response_summary import summarize_response
from app.gateway.settle_helpers import may_have_billed
from app.gateway.trace_headers import TraceHeaders
from app.playground.output import usage_cost
from app.playground.request import build_body, surface_for
from app.playground.schemas import Params
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
# Finish reasons for an answer cut off by the provider's output token limit.
_TRUNCATED = ("max_tokens", "length", "MAX_TOKENS")
JUDGE_PROVIDERS = frozenset({ProviderKind.ANTHROPIC, ProviderKind.OPENAI, ProviderKind.GEMINI})
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
        if provider not in JUDGE_PROVIDERS:
            raise EvaluatorError("judge_provider_unsupported")
        input_chars = sum(len(message.content) for message in messages)
        body = _body(provider, self.config, messages)
        estimate = estimate_judge(provider, self.config, body, self._prices, self._clock())
        # A zero price (an organization's override) would make every call free to the budget.
        if estimate is None or estimate <= 0:
            raise EvaluatorError("evaluator_model_unpriced")
        request = GatewayRequest(
            surface=surface_for(provider),
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
        cost = usage_cost(
            self._prices,
            self._credential.provider,
            result.model or self.config.model,
            result.usage,
            self._clock(),
        )
        if result.error is not None or result.status != 200 or result.body is None:
            billed = may_have_billed(result)
            charge = failed_call_charge(
                may_have_billed=billed, usage_cost=cost, estimate=prepared.estimate
            )
            raise JudgeCallError(_failure_code(result), charge)
        summary = summarize_response(prepared.request.surface, result.body)
        truncated = summary.finish_reason in _TRUNCATED
        try:
            arguments: Mapping[str, Any] | str | None
            if prepared.request.surface == "generate_content":
                arguments = None if truncated else _gemini_verdict_arguments(summary.output)
            else:
                arguments = _verdict_arguments(summary.output)
            if arguments is None:
                raise VerdictUnparseable
            verdict = parse_verdict_arguments(arguments)
        except VerdictUnparseable as error:
            charge = failed_call_charge(
                may_have_billed=True, usage_cost=cost, estimate=prepared.estimate
            )
            raise JudgeCallError("judge_truncated" if truncated else error.code, charge) from None
        return EvalResult(
            scores=[_score(prepared.input, self.name, verdict.score, verdict.reasoning)],
            cost_usd=prepared.estimate if cost is None else cost,
            reasoning=verdict.reasoning,
        )

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


def estimate_judge(
    provider: ProviderKind,
    config: JudgeConfig,
    body: dict[str, Any] | None,
    prices: PriceBook,
    at: datetime,
) -> Decimal | None:
    """Check price at configuration time and estimate spend for the rendered request."""
    if provider == ProviderKind.GEMINI:
        if body is None:
            body = _body(provider, config, [Message("user", "Evaluate the response.")])
        bound = reserve_gemini(body, [config.model], prices, at=at, fallback_cap=config.max_tokens)
        return bound.usd if bound is not None else None
    return estimate_call_cost(
        config.model,
        1 if body is None else _prompt_text(body),
        config.max_tokens,
        prices,
        provider=provider,
        at=at,
    )


def _body(provider: ProviderKind, config: JudgeConfig, messages: list[Message]) -> dict[str, Any]:
    """The provider-native body: Anthropic takes the system text apart, OpenAI first.

    `temperature` is sent only when the config sets one: the newest Claude models reject
    non-default sampling parameters. A Claude model that thinks by default gets `low` effort: a
    verdict needs little reasoning, and the thinking shares `max_tokens` with the answer.
    """
    if provider == ProviderKind.GEMINI:
        body = build_body(
            provider,
            config.model,
            Params(max_tokens=config.max_tokens, temperature=config.temperature),
            [*messages, Message("system", _USE_TOOL)],
        )
        body["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": VERDICT_TOOL_NAME,
                        "description": _VERDICT_DESCRIPTION,
                        "parametersJsonSchema": {**_VERDICT_SCHEMA, "additionalProperties": False},
                    }
                ],
            }
        ]
        body["toolConfig"] = {
            "functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [VERDICT_TOOL_NAME]},
        }
        return body
    system = [m.content for m in messages if m.role == "system"] + [_USE_TOOL]
    rest = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
    body = {"model": config.model}
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


def _gemini_verdict_arguments(output: dict[str, Any] | None) -> Mapping[str, Any] | None:
    parts = output.get("parts") if output is not None else None
    if not isinstance(parts, list):
        return None
    if any(not isinstance(part, dict) for part in parts):
        return None
    visible = [part for part in parts if part.get("thought") is not True or "functionCall" in part]
    if len(visible) != 1:
        return None
    call = visible[0].get("functionCall")
    if not isinstance(call, dict) or call.get("name") != VERDICT_TOOL_NAME:
        return None
    args = call.get("args")
    if not isinstance(args, dict) or args.keys() != {"score", "reasoning"}:
        return None
    score = args["score"]
    if (
        type(score) not in (int, float)
        or not 0 <= score <= 1
        or not isinstance(args["reasoning"], str)
    ):
        return None
    return args


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

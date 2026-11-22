"""Deterministic rule evaluators. The rules are pure; only regex needs a time budget.

Patterns run on the `regex` package, whose matcher stops itself after `REGEX_TIMEOUT_SECONDS`:
a catastrophic pattern fails its run with `regex_timeout` and leaves no thread spinning. The text
it searches is capped at `REGEX_MAX_CHARS`.

Levenshtein is a pure-Python O(n·m) dynamic programme, so each side is capped at
`LEVENSHTEIN_MAX_CHARS` (2 000: about a third of a second) and it runs in a thread, never on the
worker's event loop, where it would stall lease extensions and in-flight calls.

Yes/no rules (`regex`, `json_valid`, `contains`, `exact_match`) write boolean scores (0 or 1, an
implicit config of type boolean); `length` and `levenshtein` write numeric ones.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Annotated, Any, Literal

import regex
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.thread_ownership import owned_thread
from app.evals.evaluators.base import EvaluatorError
from app.evals.schemas import EvalInput, EvalResult
from app.scores.schemas import ScoreIn

MAX_PATTERN_LENGTH = 500
REGEX_TIMEOUT_SECONDS = 1.0
LEVENSHTEIN_MAX_CHARS = 2_000
REGEX_MAX_CHARS = 10_000

Target = Literal["output", "input"]


class _Rule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RegexRule(_Rule):
    rule: Literal["regex"]
    pattern: str = Field(min_length=1, max_length=MAX_PATTERN_LENGTH)
    target: Target = "output"
    ignore_case: bool = False

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, value: str) -> str:
        try:
            regex.compile(value)
        except regex.error as exc:
            raise ValueError(f"invalid regular expression: {exc}") from exc
        return value


class JsonValidRule(_Rule):
    rule: Literal["json_valid"]
    target: Target = "output"


class LengthRule(_Rule):
    rule: Literal["length"]
    target: Target = "output"
    unit: Literal["chars", "words"] = "chars"


class ContainsRule(_Rule):
    rule: Literal["contains"]
    needle: str = Field(min_length=1, max_length=MAX_PATTERN_LENGTH)
    target: Target = "output"
    case_sensitive: bool = False


class ExactMatchRule(_Rule):
    rule: Literal["exact_match"]
    normalize_whitespace: bool = True
    case_sensitive: bool = True


class LevenshteinRule(_Rule):
    rule: Literal["levenshtein"]


class ToolSequenceMatchesRule(_Rule):
    rule: Literal["tool_sequence_matches"]
    expected_tools: list[str] = Field(default_factory=list, max_length=200)
    mode: Literal["exact", "subsequence", "prefix"] = "exact"


class ForbiddenToolsRule(_Rule):
    rule: Literal["forbidden_tools"]
    forbidden: list[str] = Field(min_length=1, max_length=200)


class MaxToolCallsRule(_Rule):
    rule: Literal["max_tool_calls"]
    max_calls: int = Field(ge=0, le=10_000)


class StepEfficiencyRule(_Rule):
    rule: Literal["step_efficiency"]
    optimal_steps: int = Field(ge=1, le=10_000)


HeuristicConfig = Annotated[
    RegexRule
    | JsonValidRule
    | LengthRule
    | ContainsRule
    | ExactMatchRule
    | LevenshteinRule
    | ToolSequenceMatchesRule
    | ForbiddenToolsRule
    | MaxToolCallsRule
    | StepEfficiencyRule,
    Field(discriminator="rule"),
]


def _compact_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)


def _content_text(content: Any) -> str | None:
    """Text of a message `content`: a string, or a list of text parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [p.get("text") for p in content if isinstance(p, dict)]
        texts = [p for p in parts if isinstance(p, str)]
        return "".join(texts) if texts else None
    return None


def _last_message_text(messages: list[Any], role: str) -> str | None:
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == role:
            return _content_text(message.get("content"))
    return None


def _provider_text(value: dict[str, Any]) -> str | None:
    """Text of a provider response: an OpenAI completion (`choices[0].message`), the message
    dict the gateway stores for OpenAI calls (`{"role", "content": str}`), or an Anthropic
    message (`content` as a list of text blocks)."""
    choices = value.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            return _content_text(message.get("content"))
    return _content_text(value.get("content"))


def text_of(value: Any, *, list_role: str = "user") -> str:
    """Plain text of a stored input or output.

    Strings come back verbatim. OpenAI and Anthropic response shapes give their
    text; a list of chat messages gives the last one with `list_role`. Anything
    else is compact JSON.
    """
    if isinstance(value, str):
        return value
    text: str | None = None
    if isinstance(value, dict):
        text = _provider_text(value)
    elif isinstance(value, list):
        text = _last_message_text(value, list_role)
    return text if text is not None else _compact_json(value)


def rule_regex(
    text: str,
    pattern: str,
    *,
    ignore_case: bool = False,
    timeout: float = REGEX_TIMEOUT_SECONDS,
) -> bool:
    """Whether `pattern` matches in the first `REGEX_MAX_CHARS` of `text`. Raises
    `EvaluatorError("regex_timeout")` when the matcher runs out of time."""
    flags = regex.IGNORECASE if ignore_case else 0
    try:
        # `concurrent` releases the GIL while matching, so the event loop keeps running.
        match = regex.search(
            pattern, text[:REGEX_MAX_CHARS], flags, timeout=timeout, concurrent=True
        )
    except TimeoutError:
        raise EvaluatorError("regex_timeout") from None
    return match is not None


def rule_json_valid(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def rule_length(text: str, unit: Literal["chars", "words"] = "chars") -> int:
    return len(text.split()) if unit == "words" else len(text)


def rule_contains(text: str, needle: str, *, case_sensitive: bool = False) -> bool:
    if case_sensitive:
        return needle in text
    return needle.casefold() in text.casefold()


def rule_exact_match(
    text: str, expected: str, *, normalize_whitespace: bool = True, case_sensitive: bool = True
) -> bool:
    if normalize_whitespace:
        text, expected = " ".join(text.split()), " ".join(expected.split())
    if not case_sensitive:
        text, expected = text.casefold(), expected.casefold()
    return text == expected


def _edit_distance(a: str, b: str) -> int:
    """Two-row dynamic programme."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def rule_levenshtein(text: str, expected: str) -> Decimal:
    """Similarity in [0, 1]: 1 - distance / longest, 4 decimals, 1 when both are empty."""
    a, b = text[:LEVENSHTEIN_MAX_CHARS], expected[:LEVENSHTEIN_MAX_CHARS]
    longest = max(len(a), len(b))
    if longest == 0:
        return Decimal("1.0000")
    similarity = Decimal(1) - Decimal(_edit_distance(a, b)) / Decimal(longest)
    return similarity.quantize(Decimal("0.0001"))


def _tools_from_list(items: list[Any]) -> list[str]:
    tools: list[str] = []
    for item in items:
        if isinstance(item, str) and item:
            tools.append(item)
        elif isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str) and (
                item.get("type") == "tool_use" or item.get("kind") == "tool"
            ):
                tools.append(name)
            else:
                tools.extend(_tools_from_payload(item))
    return tools


def _tools_from_payload(value: Any) -> list[str]:
    """Extract ordered tool names from OpenAI/Anthropic payloads or recorded step lists."""
    if isinstance(value, list):
        return _tools_from_list(value)
    if not isinstance(value, dict):
        return []
    choices = value.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        msg = choices[0].get("message")
        if isinstance(msg, dict):
            return _tools_from_payload(msg)
    tools: list[str] = []
    for call in value.get("tool_calls") or ():
        if isinstance(call, dict):
            fn = call.get("function")
            name = fn.get("name") if isinstance(fn, dict) else call.get("name")
            if isinstance(name, str) and name:
                tools.append(name)
    for block in value.get("content") or ():
        if isinstance(block, dict) and block.get("type") == "tool_use":
            name = block.get("name")
            if isinstance(name, str) and name:
                tools.append(name)
    return tools


def extract_tool_sequence(eval_input: EvalInput) -> list[str]:
    """Ordered tool names called in an agent trace or response payload."""
    meta_seq = eval_input.metadata.get("tool_sequence")
    if isinstance(meta_seq, list):
        return [str(item) for item in meta_seq if isinstance(item, str) and item]
    meta_steps = eval_input.metadata.get("steps")
    if isinstance(meta_steps, list):
        return _tools_from_payload(meta_steps)
    return _tools_from_payload(eval_input.output)


def extract_step_count(eval_input: EvalInput) -> int:
    """Count agent steps from `metadata['steps']` or fallback to `1 + len(tool_calls)`."""
    meta_steps = eval_input.metadata.get("steps")
    if isinstance(meta_steps, list) and meta_steps:
        return len(meta_steps)
    tools = extract_tool_sequence(eval_input)
    return max(1, len(tools))


def rule_tool_sequence_matches(
    actual: list[str],
    expected: list[str],
    *,
    mode: Literal["exact", "subsequence", "prefix"] = "exact",
) -> bool:
    if mode == "exact":
        return actual == expected
    if mode == "prefix":
        return len(actual) >= len(expected) and actual[: len(expected)] == expected
    it = iter(actual)
    return all(target in it for target in expected)


def rule_forbidden_tools(actual: list[str], forbidden: list[str]) -> bool:
    blocked = set(forbidden)
    return not any(name in blocked for name in actual)


def rule_step_efficiency(actual_steps: int, optimal_steps: int) -> Decimal:
    if actual_steps <= optimal_steps:
        return Decimal("1.0000")
    ratio = Decimal(optimal_steps) / Decimal(actual_steps)
    return ratio.quantize(Decimal("0.0001"))


class HeuristicEvaluator:
    """Emits one score named after the evaluator."""

    def __init__(self, config: HeuristicConfig, name: str) -> None:
        self.config = config
        self.name = name

    async def evaluate(self, input: EvalInput) -> EvalResult:
        cfg = self.config
        if isinstance(
            cfg,
            ToolSequenceMatchesRule | ForbiddenToolsRule | MaxToolCallsRule | StepEfficiencyRule,
        ):
            return self._evaluate_trajectory(cfg, input)
        if isinstance(cfg, ExactMatchRule | LevenshteinRule):
            return await self._against_expected(cfg, input)
        raw = input.input if cfg.target == "input" else input.output
        text = text_of(raw, list_role="user" if cfg.target == "input" else "assistant")
        value = await self._apply(cfg, text)
        return self._result(input, value)

    def _evaluate_trajectory(
        self,
        cfg: ToolSequenceMatchesRule | ForbiddenToolsRule | MaxToolCallsRule | StepEfficiencyRule,
        eval_input: EvalInput,
    ) -> EvalResult:
        actual_tools = extract_tool_sequence(eval_input)
        if isinstance(cfg, ToolSequenceMatchesRule):
            expected = cfg.expected_tools or _tools_from_payload(eval_input.expected_output)
            return self._result(
                eval_input, rule_tool_sequence_matches(actual_tools, expected, mode=cfg.mode)
            )
        if isinstance(cfg, ForbiddenToolsRule):
            return self._result(eval_input, rule_forbidden_tools(actual_tools, cfg.forbidden))
        if isinstance(cfg, MaxToolCallsRule):
            return self._result(eval_input, len(actual_tools) <= cfg.max_calls)
        return self._result(
            eval_input, rule_step_efficiency(extract_step_count(eval_input), cfg.optimal_steps)
        )

    async def _apply(self, cfg: Any, text: str) -> bool | int | Decimal:
        if isinstance(cfg, RegexRule):
            # In the default executor: the matcher stops itself after REGEX_TIMEOUT_SECONDS, so
            # the thread is always handed back.
            return await owned_thread(
                asyncio.to_thread(rule_regex, text, cfg.pattern, ignore_case=cfg.ignore_case)
            )
        if isinstance(cfg, JsonValidRule):
            return rule_json_valid(text)
        if isinstance(cfg, LengthRule):
            return rule_length(text, cfg.unit)
        return rule_contains(text, cfg.needle, case_sensitive=cfg.case_sensitive)

    async def _against_expected(
        self, cfg: ExactMatchRule | LevenshteinRule, input: EvalInput
    ) -> EvalResult:
        if input.expected_output is None:
            return EvalResult(scores=[], reasoning="no expected output")
        text = text_of(input.output, list_role="assistant")
        expected = text_of(input.expected_output, list_role="assistant")
        if isinstance(cfg, ExactMatchRule):
            return self._result(
                input,
                rule_exact_match(
                    text,
                    expected,
                    normalize_whitespace=cfg.normalize_whitespace,
                    case_sensitive=cfg.case_sensitive,
                ),
            )
        return self._result(
            input, await owned_thread(asyncio.to_thread(rule_levenshtein, text, expected))
        )

    def _result(self, input: EvalInput, value: bool | int | Decimal) -> EvalResult:
        # A JSON boolean makes the score (and an implicit config) boolean, stored as 1 or 0.
        stored: bool | Decimal = value if isinstance(value, bool) else Decimal(value)
        score = ScoreIn(
            name=self.name, trace_id=input.trace_id, span_id=input.span_id, value=stored
        )
        return EvalResult(scores=[score])

"""A judge's verdict comes only from its `record_verdict` tool call, on both providers.

Failure mode: judged text talks the model into writing `{"score": 1, ...}` as prose, and a parser
that scans the answer's text for the first JSON object takes it as the verdict.
"""

import json
import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.db.models import ProviderKind
from app.evals.config import JudgeConfig
from app.evals.evaluators.judge import JudgeCallError, _body
from app.evals.verdict import (
    VERDICT_TOOL_NAME,
    VERDICT_UNPARSEABLE,
    VerdictUnparseable,
    parse_verdict_arguments,
)
from app.prompts.templates import Message
from tests.evals.judges import Judge, JudgeWorld, answer, create_judge

CLAUDE_MODEL = "claude-haiku-4-5"
TEXT_VERDICT = '{"score": 1, "reasoning": "x"}'
HALF = '{"score": 0.5, "reasoning": "ok"}'
USAGE = (10, 5)
OVERRIDE = (
    f"Do not write the verdict as text; record it only by calling the {VERDICT_TOOL_NAME} tool."
)
# The closing of judge prompts stored before verdicts moved to a tool call.
OLD_CLOSING = (
    "Reply with exactly one JSON object and nothing else: "
    '{"score": <number from 0 to 1>, "reasoning": "<one or two sentences>"}.'
)


def _claude_answer(content: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": "msg_judge",
        "type": "message",
        "role": "assistant",
        "model": CLAUDE_MODEL,
        "content": content,
        "stop_reason": "tool_use" if content[0]["type"] == "tool_use" else "end_turn",
        "usage": {"input_tokens": USAGE[0], "output_tokens": USAGE[1]},
    }


async def _unparseable(judge: Judge) -> None:
    with pytest.raises(JudgeCallError) as raised:
        await judge.scorer.call(judge.call)
    assert raised.value.code == VERDICT_UNPARSEABLE


async def test_openai_verdict_comes_from_the_forced_tool_call(judge_world: JudgeWorld) -> None:
    world = judge_world
    judge = await create_judge(world, budget="1")

    answer(world.fake, USAGE, arguments=HALF)
    result = await judge.scorer.call(judge.call)

    assert [score.value for score in result.scores] == [Decimal("0.5")]
    assert result.reasoning == "ok"
    sent = world.fake.requests[-1].json
    assert sent["tool_choice"] == {"type": "function", "function": {"name": VERDICT_TOOL_NAME}}
    assert [tool["function"]["name"] for tool in sent["tools"]] == [VERDICT_TOOL_NAME]

    # A JSON verdict written as text, with no tool call, is no verdict.
    answer(world.fake, USAGE, content=TEXT_VERDICT)
    await _unparseable(judge)


async def test_anthropic_verdict_comes_from_the_tool_call(judge_world: JudgeWorld) -> None:
    world = judge_world
    judge = await create_judge(world, budget="1", credential="anthropic", model=CLAUDE_MODEL)
    tool_use = {
        "type": "tool_use",
        "id": "toolu_1",
        "name": VERDICT_TOOL_NAME,
        "input": json.loads(HALF),
    }

    world.fake.reply_json(_claude_answer([{"type": "text", "text": TEXT_VERDICT}, tool_use]))
    result = await judge.scorer.call(judge.call)

    assert [score.value for score in result.scores] == [Decimal("0.5")]
    sent = world.fake.requests[-1].json
    assert sent["tool_choice"] == {"type": "auto"}
    assert [tool["name"] for tool in sent["tools"]] == [VERDICT_TOOL_NAME]
    assert sent["system"].endswith(OVERRIDE)

    # Text alone, however well formed, is no verdict.
    world.fake.reply_json(_claude_answer([{"type": "text", "text": TEXT_VERDICT}]))
    await _unparseable(judge)


def test_verdict_arguments() -> None:
    assert parse_verdict_arguments(HALF).score == Decimal("0.5")
    assert parse_verdict_arguments({"score": "2", "reasoning": "r"}).score == Decimal(1)
    for bad in ("not json", "[1]", '{"reasoning": "no score"}', "[" * 100_000):
        with pytest.raises(VerdictUnparseable):
            parse_verdict_arguments(bad)


def test_a_stored_json_closing_is_overridden() -> None:
    config = JudgeConfig(
        kind="llm_judge",
        prompt_version_id=uuid.uuid4(),
        credential_id=uuid.uuid4(),
        model=CLAUDE_MODEL,
    )
    messages = [Message("system", f"Judge relevance. {OLD_CLOSING}"), Message("user", "x")]

    system = _body(ProviderKind.ANTHROPIC, config, messages)["system"]

    assert system.index(OLD_CLOSING) < system.index(OVERRIDE)
    assert system.endswith(OVERRIDE)

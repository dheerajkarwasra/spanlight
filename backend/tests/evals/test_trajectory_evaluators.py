"""Unit tests for agent trajectory heuristic evaluators."""

from decimal import Decimal

from app.evals.config import parse_config
from app.evals.evaluators.heuristic import (
    ForbiddenToolsRule,
    HeuristicEvaluator,
    MaxToolCallsRule,
    ToolSequenceMatchesRule,
)
from app.evals.schemas import EvalInput


async def test_tool_sequence_matches_openai_and_subsequence_modes() -> None:
    openai_out = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {"function": {"name": "search_kb"}},
                        {"function": {"name": "check_order"}},
                    ],
                }
            }
        ]
    }
    ev_exact = HeuristicEvaluator(
        ToolSequenceMatchesRule(
            rule="tool_sequence_matches", expected_tools=["search_kb", "check_order"]
        ),
        "seq_exact",
    )
    res_exact = await ev_exact.evaluate(
        EvalInput(trace_id="a" * 32, span_id="b" * 16, input={}, output=openai_out)
    )
    assert res_exact.scores[0].value is True

    ev_sub = HeuristicEvaluator(
        ToolSequenceMatchesRule(
            rule="tool_sequence_matches", expected_tools=["search_kb"], mode="subsequence"
        ),
        "seq_sub",
    )
    res_sub = await ev_sub.evaluate(
        EvalInput(trace_id="a" * 32, span_id="b" * 16, input={}, output=openai_out)
    )
    assert res_sub.scores[0].value is True


async def test_forbidden_tools_and_max_tool_calls_from_anthropic_and_metadata() -> None:
    anthropic_out = {
        "content": [
            {"type": "tool_use", "name": "read_file", "input": {}},
            {"type": "tool_use", "name": "delete_table", "input": {}},
        ]
    }
    ev_forbidden = HeuristicEvaluator(
        ForbiddenToolsRule(rule="forbidden_tools", forbidden=["delete_table", "drop_db"]),
        "safe_tools",
    )
    r1 = await ev_forbidden.evaluate(
        EvalInput(trace_id="a" * 32, span_id=None, input={}, output=anthropic_out)
    )
    assert r1.scores[0].value is False

    ev_max = HeuristicEvaluator(MaxToolCallsRule(rule="max_tool_calls", max_calls=2), "max_calls")
    r2 = await ev_max.evaluate(
        EvalInput(trace_id="a" * 32, span_id=None, input={}, output=anthropic_out)
    )
    assert r2.scores[0].value is True


async def test_step_efficiency_and_config_parsing() -> None:
    cfg = parse_config(
        {"kind": "heuristic", "check": {"rule": "step_efficiency", "optimal_steps": 2}}
    )
    ev = HeuristicEvaluator(cfg.check, "eff")  # type: ignore[arg-type]
    r = await ev.evaluate(
        EvalInput(
            trace_id="a" * 32,
            span_id=None,
            input={},
            output={},
            metadata={
                "steps": [{"kind": "llm"}, {"kind": "tool"}, {"kind": "tool"}, {"kind": "llm"}]
            },
        )
    )
    assert r.scores[0].value == Decimal("0.5000")

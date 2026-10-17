"""`usage_state` classifies a span's usage so the UI can say why a cost is missing.

Failure mode: a span without a cost is shown as a bare "—", so a user cannot tell an exporter
that sent no token counts from a model Spanlight has no price for.
"""

from decimal import Decimal

import pytest

from app.db.models.telemetry import SpanKind
from app.ingest.usage_state import UsageState, usage_state


@pytest.mark.parametrize(
    ("kind", "model", "input_tokens", "output_tokens", "cost_usd", "usage_rollup", "expected"),
    [
        (SpanKind.LLM, "gpt-4.1", 10, 5, Decimal("0.001"), False, UsageState.PRICED),
        (SpanKind.LLM, "gpt-4.1", None, None, None, True, UsageState.ROLLUP),
        (SpanKind.LLM, "gpt-4.1", None, None, None, False, UsageState.USAGE_NOT_REPORTED),
        (SpanKind.LLM, "gpt-4.1", 10, None, None, False, UsageState.PARTIAL_USAGE),
        (SpanKind.LLM, None, 10, 5, None, False, UsageState.MODEL_UNKNOWN),
        (SpanKind.LLM, "unknown-model", 10, 5, None, False, UsageState.NO_PRICE),
        (SpanKind.CHAIN, None, None, None, None, False, UsageState.NOT_APPLICABLE),
    ],
)
def test_every_row_of_the_usage_state_table(
    kind: SpanKind,
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cost_usd: Decimal | None,
    usage_rollup: bool,
    expected: UsageState,
) -> None:
    assert (
        usage_state(
            kind=kind,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            usage_rollup=usage_rollup,
        )
        == expected
    )


def test_rollup_with_a_cost_is_rollup_not_priced() -> None:
    state = usage_state(
        kind=SpanKind.LLM,
        model="gpt-4.1",
        input_tokens=10,
        output_tokens=5,
        cost_usd=Decimal("0.001"),
        usage_rollup=True,
    )
    assert state == UsageState.ROLLUP


def test_tool_span_is_not_applicable() -> None:
    state = usage_state(
        kind=SpanKind.TOOL,
        model="gpt-4.1",
        input_tokens=10,
        output_tokens=5,
        cost_usd=None,
        usage_rollup=False,
    )
    assert state == UsageState.NOT_APPLICABLE


def test_llm_with_only_output_tokens_is_partial() -> None:
    state = usage_state(
        kind=SpanKind.LLM,
        model="gpt-4.1",
        input_tokens=None,
        output_tokens=5,
        cost_usd=None,
        usage_rollup=False,
    )
    assert state == UsageState.PARTIAL_USAGE


def test_model_unknown_beats_usage_not_reported() -> None:
    """A span with no model and no usage is `model_unknown`: a price needs the model first."""
    state = usage_state(
        kind=SpanKind.LLM,
        model=None,
        input_tokens=None,
        output_tokens=None,
        cost_usd=None,
        usage_rollup=False,
    )
    assert state == UsageState.MODEL_UNKNOWN

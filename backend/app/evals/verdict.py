"""Read a judge's verdict out of its `record_verdict` tool call. Pure: no database, no I/O.

The judge call offers one tool, `record_verdict`, whose arguments are the verdict:
`{"score": <0..1>, "reasoning": "..."}`. The verdict is read from those arguments only, never from
the answer's text, so judged content that talks the model into writing a JSON verdict as prose
cannot supply one. `score` is a number (a numeric string such as `"0.8"` is accepted), clamped to
0..1 and kept to 4 decimals; `reasoning` is a string cut to 1 000 characters, empty when absent.
Anything else is `VerdictUnparseable`.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

from app.core.text import clean_text

MAX_REASONING_CHARS = 1000
VERDICT_UNPARSEABLE = "verdict_unparseable"
VERDICT_TOOL_NAME = "record_verdict"

_PLACES = Decimal("0.0001")
_ZERO = Decimal(0)
_ONE = Decimal(1)


@dataclass(frozen=True, slots=True)
class Verdict:
    score: Decimal
    reasoning: str


class VerdictUnparseable(ValueError):  # noqa: N818 - the name the spec pins
    """The answer holds no verdict tool call with a usable score. `code` is the run's error."""

    code = VERDICT_UNPARSEABLE

    def __init__(self) -> None:
        super().__init__(VERDICT_UNPARSEABLE)


def parse_verdict_arguments(arguments: Mapping[str, Any] | str) -> Verdict:
    """The verdict in a `record_verdict` call's arguments: OpenAI's JSON string or Anthropic's
    input object. Raises `VerdictUnparseable`."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (ValueError, RecursionError):  # deep nesting overflows the decoder
            raise VerdictUnparseable from None
    if not isinstance(arguments, Mapping):
        raise VerdictUnparseable
    score = _score(arguments.get("score"))
    reasoning = arguments.get("reasoning")
    if reasoning is None:
        reasoning = ""
    if score is None or not isinstance(reasoning, str):
        raise VerdictUnparseable
    return Verdict(score=score, reasoning=clean_text(reasoning).strip()[:MAX_REASONING_CHARS])


def _score(raw: object) -> Decimal | None:
    """`raw` as a score in 0..1 with 4 decimals; None when it is not a finite number."""
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        return None
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    clamped = min(max(value, _ZERO), _ONE)
    return clamped.quantize(_PLACES, rounding=ROUND_HALF_EVEN)

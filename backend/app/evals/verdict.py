"""Read a judge's verdict out of the model's answer. Pure: no database, no I/O.

The judge templates ask for exactly one JSON object, `{"score": <0..1>, "reasoning": "..."}`, but
models wrap it in prose or code fences often enough that the answer is scanned instead of parsed
whole: the first balanced `{...}` that is valid JSON is the verdict. Its `score` is a number (a
numeric string such as `"0.8"` is accepted), clamped to 0..1 and kept to 4 decimals; `reasoning`
is a string cut to 1 000 characters, empty when absent. Anything else is `VerdictUnparseable`.
"""

import json
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any

from app.core.text import clean_text

MAX_REASONING_CHARS = 1000
MAX_SCAN_CHARS = 50_000  # 4 096 output tokens stay far below it
MAX_CANDIDATES = 64
VERDICT_UNPARSEABLE = "verdict_unparseable"

_PLACES = Decimal("0.0001")
_ZERO = Decimal(0)
_ONE = Decimal(1)


@dataclass(frozen=True, slots=True)
class Verdict:
    score: Decimal
    reasoning: str


class VerdictUnparseable(ValueError):  # noqa: N818 - the name the spec pins
    """The answer holds no JSON object with a usable score. `code` is the run's error."""

    code = VERDICT_UNPARSEABLE

    def __init__(self) -> None:
        super().__init__(VERDICT_UNPARSEABLE)


def parse_verdict(text: str) -> Verdict:
    """The verdict in `text`. Raises `VerdictUnparseable`."""
    found = first_json_object(text)
    if found is None:
        raise VerdictUnparseable
    score = _score(found.get("score"))
    reasoning = found.get("reasoning")
    if reasoning is None:
        reasoning = ""
    if score is None or not isinstance(reasoning, str):
        raise VerdictUnparseable
    return Verdict(score=score, reasoning=clean_text(reasoning).strip()[:MAX_REASONING_CHARS])


def first_json_object(text: str) -> dict[str, Any] | None:
    """The first balanced `{...}` in `text` that parses as a JSON object, or None.

    Braces inside JSON strings do not count, so `{"reasoning": "a } b"}` is one object. When a
    candidate does not close or fails to parse, the scan resumes at the next `{` after its opening
    brace. At most `MAX_CANDIDATES` openings in the first `MAX_SCAN_CHARS` characters are tried,
    which bounds the work on an answer full of stray braces.
    """
    text = text[:MAX_SCAN_CHARS]
    start = text.find("{")
    for _ in range(MAX_CANDIDATES):
        if start == -1:
            return None
        end = _matching_brace(text, start)
        value: object = None
        if end is not None:
            try:
                value = json.loads(text[start : end + 1])
            except ValueError:
                value = None
        if isinstance(value, dict):
            return value
        start = text.find("{", start + 1)
    return None


def _matching_brace(text: str, start: int) -> int | None:
    """The index of the `}` that closes the `{` at `start`, skipping JSON strings."""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


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

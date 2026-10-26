"""Secret redaction for captured payloads.

Applied to every string inside span input/output/attributes before storage.
Patterns are deliberately conservative: a missed secret is worse than a
redacted harmless string, but we avoid patterns broad enough to eat ordinary
prose (e.g. bare 40-char strings are not treated as AWS secrets unless they
are labelled as such).
"""

import re
from functools import lru_cache
from typing import Any

from app.core.security import ANY_KEY_PATTERN

REDACTED = "[REDACTED]"

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Spanlight credentials of every kind (API keys, personal access tokens, gateway keys). A
    # payload readable by project viewers and read keys must never hold one: a token there would
    # hand its owner's access to everyone who can read spans. The shape comes from `security`.
    ANY_KEY_PATTERN,
    # Anthropic keys (must precede the generic sk- pattern).
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    # OpenAI-style keys, including sk-proj-… and sk-svcacct-….
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    # Authorization header values.
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{8,}"),
    # AWS access key ids.
    re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"),
    # Labelled AWS secret access keys, e.g. `aws_secret_access_key = …`.
    re.compile(r"(?i)aws_secret_access_key[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9/+=]{40}"),
)

# A card-shaped run: digit groups separated by single spaces or dashes, every group of four or
# more digits but the last, which has two or more. That covers how cards are printed (4-4-4-4,
# 4-6-5, 4-6-4, 4-4-4-2, 4-4-4-4-3, or unbroken) and leaves lists of small numbers, dates and
# phone numbers alone. A card typed in groups of one to three digits is not detected.
#
# A card number is 13-19 digits of whole groups inside a run: a CVV, an expiry or a quantity next
# to a card makes the run longer than the card, so the run is searched for its card rather than
# judged whole.
_DIGIT_RUN = re.compile(r"\b\d{4,}(?:[ \-]\d{4,})*(?:[ \-]\d{2,})?\b")
_SEPARATOR = re.compile(r"[ \-]")
_CARD_DIGITS = range(13, 20)

# Contact patterns, applied only for a project that opts in (`projects.redact_contact_info`).
# An email needs a dotted domain ending in letters, so `react@18.3.1` and `user@localhost` are
# kept; an SSH remote like `git@github.com` is redacted. Every quantifier is bounded and a match
# starts only where a local part starts, so a long run without an `@` costs one pass.
_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]{1,64}"
    r"@(?:[A-Za-z0-9\-]{1,63}\.){1,8}[A-Za-z]{2,24}\b"
)
# A phone candidate: `+`, a non-zero digit, then digits and the separators numbers are printed
# with, ending on a digit. `_redact_phone` keeps it unless it holds 10-15 digits (E.164 allows at
# most 15). The `+` is required: national formats like `(415) 555-2671` are not detected. The
# 10-digit floor keeps `+12345678` in a diff; the non-zero digit keeps `+05:30` in a timestamp.
_PHONE = re.compile(r"(?<![\w+])\+[1-9][\d ().\-]{8,22}\d(?!\d)")
_PHONE_DIGITS = range(10, 16)

MAX_DEPTH = 64
# What a value nested deeper than `MAX_DEPTH` is stored as.
DEPTH_MARKER = "[MAX_DEPTH]"


# A digit's Luhn value when doubled, indexed by the digit.
_DOUBLED = (0, 2, 4, 6, 8, 1, 3, 5, 7, 9)


def luhn_valid(digits: str) -> bool:
    values = [int(char) for char in digits]  # `int`, so any Unicode decimal digit reads right
    return (sum(values[-1::-2]) + sum(_DOUBLED[value] for value in values[-2::-2])) % 10 == 0


# Called only for groups of at most 19 digits, so the cache never holds a long string.
@lru_cache(maxsize=4096)
def _group_sums(group: str) -> tuple[int, int]:
    """The group's Luhn sum when its last digit is not doubled, and when it is."""
    values = [int(char) for char in group]
    odd, even = values[-1::-2], values[-2::-2]
    return (
        sum(odd) + sum(_DOUBLED[value] for value in even),
        sum(_DOUBLED[value] for value in odd) + sum(even),
    )


def _redact_cards(match: re.Match[str]) -> str:
    """The run with every Luhn-valid card of 13-19 digits made of whole groups redacted.

    Every window of whole groups is checked, and overlapping cards are all covered, so no digit
    of any of them is kept. A window ends at each group and holds at most five groups (19 digits
    in groups of four or more), so the cost is a few steps per group: about 1 s of CPU for a
    5 MiB body of random four-digit groups, the costliest shape because every group misses the
    `_group_sums` cache.
    """
    run = match.group(0)
    if len(run) < _CARD_DIGITS.start:
        return run
    groups = _SEPARATOR.split(run)
    if len(groups) == 1:
        return REDACTED if len(run) in _CARD_DIGITS and luhn_valid(run) else run
    # A group longer than a card is in no window; skipping it also keeps long strings out of the
    # cache.
    longest = _CARD_DIGITS.stop - 1
    sums = [_group_sums(group) if len(group) <= longest else (0, 0) for group in groups]
    lengths = [len(group) for group in groups]
    covered = [False] * len(groups)
    for last in range(len(groups)):
        digits = total = 0
        for start in range(last, -1, -1):
            # Counted from the right of the window, the group's last digit is doubled when an
            # odd number of digits follows it.
            total += sums[start][digits % 2]
            digits += lengths[start]
            if digits > longest:
                break
            if digits >= _CARD_DIGITS.start and total % 10 == 0:
                covered[start : last + 1] = [True] * (last + 1 - start)
    return _rebuild_run(run, groups, covered)


def _rebuild_run(run: str, groups: list[str], covered: list[bool]) -> str:
    """`run` with each stretch of covered groups, and the separators inside it, as one marker.

    `groups` is `run` split on its separators, one character each, so a group's position in `run`
    follows from the lengths before it.
    """
    pieces: list[str] = []
    position = 0
    for index, group in enumerate(groups):
        joined_to_previous = index > 0 and covered[index] and covered[index - 1]
        if index > 0 and not joined_to_previous:
            pieces.append(run[position - 1])
        if not joined_to_previous:
            pieces.append(REDACTED if covered[index] else group)
        position += len(group) + 1
    return "".join(pieces)


def _redact_phone(match: re.Match[str]) -> str:
    candidate = match.group(0)
    digits = sum(char.isdigit() for char in candidate)
    return REDACTED if digits in _PHONE_DIGITS else candidate


def redact_text(value: str, *, contacts: bool = False) -> str:
    """`value` with secrets and card numbers replaced; also emails and phones with `contacts`."""
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(REDACTED, value)
    if contacts:
        value = _PHONE.sub(_redact_phone, _EMAIL.sub(REDACTED, value))
    return _DIGIT_RUN.sub(_redact_cards, value)


def redact_json(value: Any, *, depth: int = 0, contacts: bool = False) -> Any:
    """Return a copy of a JSON-compatible value with secrets in strings replaced."""
    if isinstance(value, str):
        return redact_text(value, contacts=contacts)
    if depth >= MAX_DEPTH:
        return DEPTH_MARKER
    if isinstance(value, dict):
        return {
            redact_text(str(key), contacts=contacts): redact_json(
                item, depth=depth + 1, contacts=contacts
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_json(item, depth=depth + 1, contacts=contacts) for item in value]
    return value

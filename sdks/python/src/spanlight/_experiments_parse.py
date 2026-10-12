"""Parsing the experiment runner's command-line values. Pure: no I/O."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# Prompt names never contain `@` or `:`, so either one can only start a label or a version.
_VERSION = re.compile(r"^[1-9][0-9]{0,9}$")
_LABEL = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_SCORE_NAME = re.compile(r"^[^\s=,]{1,100}$")


@dataclass(frozen=True)
class PromptRef:
    """A prompt and the version to run: a label, a version number, or neither (``production``)."""

    name: str
    label: str | None
    version: int | None


def parse_fail_below(spec: str) -> dict[str, Decimal]:
    """Parse ``relevance=0.8,format=1`` into ``{"relevance": 0.8, "format": 1}``.

    An empty spec means no thresholds. Raises ``ValueError`` for a malformed pair, a repeated
    score name or a value that is not a finite number.
    """
    thresholds: dict[str, Decimal] = {}
    for pair in spec.split(","):
        pair = pair.strip()
        if not pair:
            continue
        name, separator, raw = pair.partition("=")
        name, raw = name.strip(), raw.strip()
        if not separator or not _SCORE_NAME.match(name):
            raise ValueError(f"expected score=number, got {pair!r}")
        if name in thresholds:
            raise ValueError(f"score {name!r} appears twice")
        try:
            value = Decimal(raw)
        except InvalidOperation:
            raise ValueError(f"threshold for {name!r} is not a number: {raw!r}") from None
        if not value.is_finite():
            raise ValueError(f"threshold for {name!r} is not a finite number: {raw!r}")
        thresholds[name] = value
    return thresholds


def parse_prompt_ref(ref: str) -> PromptRef:
    """Parse ``support/answer@production``, ``support/answer:3`` or a bare ``support/answer``."""
    ref = ref.strip()
    if "@" in ref and ":" in ref:
        raise ValueError(f"give a label or a version, not both: {ref!r}")
    if "@" in ref:
        name, _, label = ref.rpartition("@")
        if not name or not _LABEL.match(label):
            raise ValueError(f"not a prompt label reference: {ref!r}")
        return PromptRef(name, label, None)
    if ":" in ref:
        name, _, number = ref.rpartition(":")
        if not name or not _VERSION.match(number):
            raise ValueError(f"not a prompt version reference: {ref!r}")
        return PromptRef(name, None, int(number))
    if not ref:
        raise ValueError("the prompt reference is empty")
    return PromptRef(ref, None, None)

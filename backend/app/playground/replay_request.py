"""The request one replay sends. Pure: no database, no I/O (spec §6.3).

The body is the stored request (or the user's edit of it) with every key passed through
untouched except the ones that would change the bill or the recording: `stream` and
`stream_options` are removed, `n` is forced to 1, the output cap is clamped to
`MAX_OUTPUT_TOKENS`, and `model` is replaced by an override of the same provider family. Every
change is recorded in `changed`; a replay is `exact` only when nothing but the streaming flags
moved, since those never change what the model is asked (`IGNORED_INPUT_KEYS`).
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.db.models import ProviderKind
from app.gateway.errors import Surface
from app.playground.replay_errors import REFUSALS, ReplayRefusedError
from app.playground.replay_source import (
    Fidelity,
    ReplaySource,
    accepts,
    contains_markers,
    is_request_body,
)
from app.playground.request import DEFAULT_ANTHROPIC_MAX_TOKENS
from app.playground.schemas import MAX_OUTPUT_TOKENS

MAX_EDITED_BODY_BYTES = 256 * 1024

# Every output cap a provider body may carry: chat completions (both spellings), Responses.
_OUTPUT_CAP_KEYS = ("max_tokens", "max_completion_tokens", "max_output_tokens")
_STREAM_KEYS = ("stream", "stream_options")
_EXACT_CHANGES = frozenset(_STREAM_KEYS)


@dataclass(frozen=True)
class ReplayPlan:
    body: dict[str, Any]
    surface: Surface
    model: str
    fidelity: Fidelity
    changed: tuple[str, ...]
    max_tokens: int
    """The most output tokens the call can bill for (the budget's worst case)."""


def _refuse(code: str, **fmt: str) -> ReplayRefusedError:
    return ReplayRefusedError(code, REFUSALS[code].format(**fmt))


def _edited(edited_body: Mapping[str, Any]) -> dict[str, Any]:
    """Rules 4, 5 and 7 again on the user's edit: markers cannot be sent, the shape stays a
    request body."""
    text = json.dumps(edited_body, ensure_ascii=False)
    if len(text.encode("utf-8", "surrogatepass")) > MAX_EDITED_BODY_BYTES:
        raise _refuse("REPLAY_BODY_TOO_LARGE")
    if contains_markers(edited_body):
        raise _refuse("REPLAY_PAYLOAD_REDACTED")
    if not is_request_body(edited_body):
        raise _refuse("REPLAY_UNSUPPORTED_INPUT")
    return dict(edited_body)


def _clamp_caps(
    body: dict[str, Any], surface: Surface, override: int | None, changed: list[str]
) -> int:
    """Clamp every output cap in `body` to `min(stored, override, MAX_OUTPUT_TOKENS)`; give the
    `messages` surface its default when it has none. Returns the worst case that remains."""
    ceiling = min(override or MAX_OUTPUT_TOKENS, MAX_OUTPUT_TOKENS)
    worst = ceiling
    present = [key for key in _OUTPUT_CAP_KEYS if key in body]
    if not present and surface == "messages":
        worst = min(DEFAULT_ANTHROPIC_MAX_TOKENS, ceiling)
        body["max_tokens"] = worst
        changed.append("max_tokens")
        return worst
    for key in present:
        stored = body[key]
        if isinstance(stored, bool) or not isinstance(stored, int):
            raise _refuse("REPLAY_UNSUPPORTED_INPUT")
        body[key] = min(stored, ceiling)
        if body[key] != stored:
            changed.append(key)
        worst = min(worst, body[key])
    return worst


def build_replay(
    source: ReplaySource,
    *,
    credential_provider: ProviderKind,
    model_override: str | None,
    max_tokens_override: int | None,
    edited_body: Mapping[str, Any] | None,
) -> ReplayPlan:
    """Raises `ReplayRefusedError` for a credential of another family, an edited body that holds
    markers, is not a request body or is over 256 KB, and a source with no model to send."""
    if not accepts(source.family, credential_provider):
        raise _refuse("REPLAY_PROVIDER_MISMATCH", provider=source.family)
    changed: list[str] = []
    if edited_body is None:
        body = dict(source.body)
    else:
        body = _edited(edited_body)
        changed.append("body")
    for key in _STREAM_KEYS:
        if key in body:
            del body[key]
            changed.append(key)
    if isinstance(body.get("n"), int) and body["n"] > 1:
        body["n"] = 1
        changed.append("n")
    max_tokens = _clamp_caps(body, source.surface, max_tokens_override, changed)
    if model_override is not None and model_override != body.get("model"):
        body["model"] = model_override
        changed.append("model")
    model = body.get("model")
    if not isinstance(model, str):
        raise _refuse("REPLAY_UNSUPPORTED_INPUT")
    fidelity: Fidelity = "modified"
    if source.fidelity == "messages_only":
        fidelity = "messages_only"
    elif source.fidelity == "exact" and set(changed) <= _EXACT_CHANGES:
        fidelity = "exact"
    # shortcut: an edited body keeps the stored surface; the family check above is what stops a
    # provider switch. Recompute from shape if the UI ever lets an edit change surfaces.
    return ReplayPlan(body, source.surface, model, fidelity, tuple(changed), max_tokens)

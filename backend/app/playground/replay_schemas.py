"""What a client sends to replay a stored request, and what comes back (spec §6.7)."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from app.db.models import ReplayRun
from app.gateway.errors import Surface
from app.ingest.schemas import TraceId
from app.playground.replay_source import CredentialFamily, Fidelity, Refusal
from app.playground.schemas import MAX_OUTPUT_TOKENS, PlaygroundResult
from app.scores.schemas import SpanRef


def _no_nul(body: dict[str, Any]) -> dict[str, Any]:
    """Postgres text cannot hold NUL, and a provider has no use for it either."""
    stack: list[Any] = [body]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if "\x00" in item:
                raise ValueError("must not contain NUL characters")
        elif isinstance(item, Mapping):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return body


class ReplayIn(BaseModel):
    """The body of `POST /playground/replays`: which span, which credential, and what to change.

    `edited_body` replaces the stored request as a whole; its size (256 KB) is checked by the
    service with the replay's other refusals (`REPLAY_BODY_TOO_LARGE`), not here.
    """

    model_config = ConfigDict(extra="forbid")

    trace_id: TraceId
    span_id: SpanRef
    credential_id: uuid.UUID
    model: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    max_tokens: Annotated[int, Field(ge=1, le=MAX_OUTPUT_TOKENS)] | None = None
    edited_body: Annotated[dict[str, Any], AfterValidator(_no_nul)] | None = None


@dataclass(frozen=True)
class SourceView:
    """The replayed span and the answer it recorded, shown beside the new one."""

    trace_id: str
    span_id: str
    output_text: str | None


@dataclass(frozen=True)
class ReplayOutcome:
    """One finished replay: the ledger row's id, what was sent, and the call's result.

    `same_request_hash` is None when the replayed call's span was not stored, so the two
    requests could not be compared.
    """

    replay_id: uuid.UUID
    fidelity: Fidelity
    changed: tuple[str, ...]
    same_request_hash: bool | None
    source: SourceView
    result: PlaygroundResult


@dataclass(frozen=True)
class ReplaySourceView:
    """The read-only classification of a span (`GET …/replay-source`): what a replay would
    send, or why it is refused, and the span's last replays."""

    fidelity: Fidelity | None
    surface: Surface | None
    provider_kind: CredentialFamily | None
    body: dict[str, Any] | None
    refusal: Refusal | None
    replays: list[ReplayRun]

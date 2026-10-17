"""The replay routes' responses (spec §6.7). The request body is `app.playground.replay_schemas`."""

import uuid
from datetime import datetime
from typing import Any

from app.api.schemas.common import ApiModel, Money
from app.api.schemas.playground import UsageOut
from app.db.models import ReplayRun
from app.playground.replay_schemas import ReplayIn, ReplayOutcome, ReplaySourceView

__all__ = ["ReplayIn", "ReplayOut", "ReplaySourceOut", "ReplaySummaryOut"]


class ReplaySummaryOut(ApiModel):
    """One row of the replay ledger."""

    replay_id: uuid.UUID
    requested_by: uuid.UUID | None
    model: str
    fidelity: str
    # `running`, `done` or `failed`; `error` names the run-level failure.
    status: str
    error: str | None
    # What the replay was charged once settled (0 while running).
    cost_usd: Money
    started_at: datetime
    finished_at: datetime | None
    result_trace_id: str | None
    result_span_id: str | None

    @classmethod
    def of(cls, row: ReplayRun) -> "ReplaySummaryOut":
        return cls(
            replay_id=row.id,
            requested_by=row.requested_by,
            model=row.model,
            fidelity=row.fidelity,
            status=row.status,
            error=row.error,
            cost_usd=row.cost_usd,
            started_at=row.started_at,
            finished_at=row.finished_at,
            result_trace_id=row.result_trace_id,
            result_span_id=row.result_span_id,
        )


class RefusalOut(ApiModel):
    code: str
    detail: str


class ReplaySourceOut(ApiModel):
    """What a replay of the span would send, or why it is refused (then the first four fields
    are null), and the span's last replays."""

    fidelity: str | None
    surface: str | None
    # The credential family the request needs: `openai` (any OpenAI-compatible server) or
    # `anthropic`.
    provider_kind: str | None
    # The stored, already-redacted request.
    body: dict[str, Any] | None
    refusal: RefusalOut | None
    replays: list[ReplaySummaryOut]

    @classmethod
    def of(cls, view: ReplaySourceView) -> "ReplaySourceOut":
        refusal = view.refusal
        return cls(
            fidelity=view.fidelity,
            surface=view.surface,
            provider_kind=view.provider_kind,
            body=view.body,
            refusal=None
            if refusal is None
            else RefusalOut(code=refusal.code, detail=refusal.detail),
            replays=[ReplaySummaryOut.of(row) for row in view.replays],
        )


class ReplaySourceRefOut(ApiModel):
    trace_id: str
    span_id: str
    # The answer the source span recorded, for the side-by-side view.
    output_text: str | None


class ReplayOut(ApiModel):
    """One finished replay. `error` set means no `output`; `cost_usd`, `usage`, `latency_ms`
    and `ttft_ms` follow the playground's result."""

    replay_id: uuid.UUID
    fidelity: str
    changed: list[str]
    # Whether the replayed span's request hash equals the source's; null when the replayed
    # call's span was not stored, so the two could not be compared.
    same_request_hash: bool | None
    source: ReplaySourceRefOut
    output: str | None
    usage: UsageOut | None
    cost_usd: Money | None
    latency_ms: float | None
    # Always null: replays are not streamed.
    ttft_ms: float | None
    trace_id: str | None
    span_id: str | None
    # `BUDGET_EXCEEDED` (a project budget blocked the call), `replay_failed`, or the provider's
    # failure.
    error: str | None

    @classmethod
    def of(cls, outcome: ReplayOutcome) -> "ReplayOut":
        result = outcome.result
        return cls(
            replay_id=outcome.replay_id,
            fidelity=outcome.fidelity,
            changed=list(outcome.changed),
            same_request_hash=outcome.same_request_hash,
            source=ReplaySourceRefOut(
                trace_id=outcome.source.trace_id,
                span_id=outcome.source.span_id,
                output_text=outcome.source.output_text,
            ),
            output=result.output,
            usage=UsageOut.of(result.usage) if result.usage is not None else None,
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
            ttft_ms=result.ttft_ms,
            trace_id=result.trace_id,
            span_id=result.span_id,
            error=result.error,
        )

"""The Doctor's feedback: a member's "Was this right?" vote on an insight, and the precision of
each detector kind measured from those votes."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.db.models import InsightFeedback
from app.insights.catalogue import KIND_INFO
from app.insights.feedback_queries import FeedbackSummary, Verdict
from app.insights.precision import PrecisionRow

__all__ = [
    "MAX_NOTE_LENGTH",
    "FeedbackIn",
    "FeedbackMineOut",
    "FeedbackOut",
    "FeedbackSummaryOut",
    "PrecisionKindOut",
    "PrecisionOut",
    "feedback_out",
    "feedback_summary_out",
    "precision_kind_out",
    "precision_out",
]

MAX_NOTE_LENGTH = 500

Note = Annotated[str, StringConstraints(max_length=MAX_NOTE_LENGTH)]


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    # At most 500 characters; an empty note is stored as no note.
    note: Note | None = None


class FeedbackOut(BaseModel):
    """The caller's vote as stored. `updated_at` moves on every vote."""

    verdict: Verdict
    note: str | None
    updated_at: datetime


class FeedbackMineOut(BaseModel):
    """The caller's own vote. `updated_at` is when it was last given (the "on 14 Oct" line)."""

    verdict: Verdict
    note: str | None
    updated_at: datetime


class FeedbackSummaryOut(BaseModel):
    """The caller's own vote (`null` when they have not voted) and every member's counts."""

    mine: FeedbackMineOut | None
    right: int
    wrong: int


class PrecisionKindOut(BaseModel):
    """One detector kind's votes. The ratios are decimal strings to four places, `null` when the
    kind has no votes (`n` is 0)."""

    kind: str
    # The catalogue's name for the kind (the kind itself when the catalogue no longer knows it).
    label: str
    right: int
    wrong: int
    n: int
    precision: str | None
    wilson_low: str | None
    wilson_high: str | None
    meets_target: bool = Field(
        description="precision >= 0.8, Wilson 95 % lower bound >= 0.6 and n >= 20."
    )


class PrecisionOut(BaseModel):
    kinds: list[PrecisionKindOut]


def _verdict(value: str) -> Verdict:
    if value == "right":
        return "right"
    if value == "wrong":
        return "wrong"
    raise ValueError(f"unknown verdict {value!r}")


def feedback_out(row: InsightFeedback) -> FeedbackOut:
    return FeedbackOut(verdict=_verdict(row.verdict), note=row.note, updated_at=row.updated_at)


def feedback_summary_out(summary: FeedbackSummary) -> FeedbackSummaryOut:
    mine = summary.mine
    return FeedbackSummaryOut(
        mine=None
        if mine is None
        else FeedbackMineOut(
            verdict=_verdict(mine.verdict), note=mine.note, updated_at=mine.updated_at
        ),
        right=summary.right,
        wrong=summary.wrong,
    )


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def precision_kind_out(row: PrecisionRow) -> PrecisionKindOut:
    info = KIND_INFO.get(row.kind)
    return PrecisionKindOut(
        kind=row.kind,
        label=row.kind if info is None else info.label,
        right=row.right,
        wrong=row.wrong,
        n=row.n,
        precision=_decimal(row.precision),
        wilson_low=_decimal(row.wilson_low),
        wilson_high=_decimal(row.wilson_high),
        meets_target=row.meets_target,
    )


def precision_out(rows: list[PrecisionRow]) -> PrecisionOut:
    return PrecisionOut(kinds=[precision_kind_out(row) for row in rows])

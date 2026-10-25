"""Agreement between human reviewers and an LLM judge (UX Phase 6).

`kappa`, `f1` and `pass_threshold` are JSON numbers (4 places), like score values; each is `null`
when it cannot be computed, never zero. Matrix rows are the reviewer, columns the judge.
"""

import uuid
from typing import Self

from app.api.schemas.common import ApiModel
from app.api.schemas.scores import ScoreNumber
from app.db.models import Evaluator
from app.evals.agreement import MIN_PAIRS, Agreement, AgreementStatus, KappaKind
from app.scores.types import ScoreDataType


class AgreementOut(ApiModel):
    evaluator_id: uuid.UUID
    score_name: str
    data_type: ScoreDataType | None
    status: AgreementStatus
    pairs: int
    min_pairs: int
    capped: bool
    kappa: ScoreNumber | None
    kappa_kind: KappaKind | None
    f1: ScoreNumber | None
    pass_threshold: ScoreNumber | None
    labels: list[str]
    matrix: list[list[int]]
    flagged: bool

    @classmethod
    def of(
        cls,
        evaluator: Evaluator,
        data_type: ScoreDataType | None,
        result: Agreement,
        *,
        capped: bool,
    ) -> Self:
        return cls(
            evaluator_id=evaluator.id,
            score_name=evaluator.name,
            data_type=data_type,
            status=result.status,
            pairs=result.pairs,
            min_pairs=MIN_PAIRS,
            capped=capped,
            kappa=result.kappa,
            kappa_kind=result.kappa_kind,
            f1=result.f1,
            pass_threshold=result.pass_threshold,
            labels=list(result.labels),
            matrix=[list(row) for row in result.matrix],
            flagged=result.flagged,
        )


class AgreementSummaryOut(ApiModel):
    evaluator_id: uuid.UUID
    score_name: str
    status: AgreementStatus
    pairs: int
    kappa: ScoreNumber | None
    flagged: bool

    @classmethod
    def of(cls, evaluator: Evaluator, result: Agreement) -> Self:
        return cls(
            evaluator_id=evaluator.id,
            score_name=evaluator.name,
            status=result.status,
            pairs=result.pairs,
            kappa=result.kappa,
            flagged=result.flagged,
        )


class AgreementSummaryList(ApiModel):
    items: list[AgreementSummaryOut]

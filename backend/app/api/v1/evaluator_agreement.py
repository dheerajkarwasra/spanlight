"""How closely an LLM judge agrees with human reviewers, computed on read (UX Phase 6).

Any project member may read it (`project:read`). The summary has its own path,
`/evaluator-agreement`, so it never competes with `/evaluators/{evaluator_id}`. Pairing, scales
and thresholds are in `app.evals.agreement_queries` and `app.evals.agreement`.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import Access, DbSession, require
from app.api.schemas.evaluator_agreement import (
    AgreementOut,
    AgreementSummaryList,
    AgreementSummaryOut,
)
from app.core.errors import not_found
from app.core.permissions import Permission
from app.db.models import Evaluator
from app.evals import agreement_queries, queries
from app.evals.agreement import MAX_PAIRS, Agreement, agreement
from app.scores.types import ScoreDataType

router = APIRouter(prefix="/projects/{project_id}", tags=["evaluators"])

AgreementReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]


async def _measure(
    db: DbSession, project_id: uuid.UUID, evaluators: list[Evaluator]
) -> dict[uuid.UUID, tuple[ScoreDataType | None, Agreement, bool]]:
    """Each evaluator's score data type, agreement and whether its pairs were capped."""
    pair_sets = await agreement_queries.pair_scores(
        db, project_id, [evaluator.id for evaluator in evaluators], MAX_PAIRS
    )
    configs = await agreement_queries.score_configs(
        db, project_id, [evaluator.name for evaluator in evaluators]
    )
    measured = {}
    for evaluator in evaluators:
        config, found = configs.get(evaluator.name), pair_sets[evaluator.id]
        result = agreement(
            found.pairs,
            data_type=config.data_type if config else None,
            min_value=config.min_value if config else None,
            max_value=config.max_value if config else None,
        )
        measured[evaluator.id] = (config.data_type if config else None, result, found.capped)
    return measured


@router.get(
    "/evaluators/{evaluator_id}/agreement",
    response_model=AgreementOut,
    summary="Get an evaluator's agreement with reviewers",
)
async def get_agreement(
    project_id: uuid.UUID, evaluator_id: uuid.UUID, access: AgreementReader, db: DbSession
) -> AgreementOut:
    """Agreement over the newest 2,000 traces that both a reviewer and this evaluator scored.
    `kappa` and `f1` are `null` below `min_pairs` or when they are undefined."""
    project = access.require_project()
    evaluator = await queries.get_evaluator(db, project.id, evaluator_id)
    if evaluator is None:
        raise not_found()
    data_type, result, capped = (await _measure(db, project.id, [evaluator]))[evaluator.id]
    return AgreementOut.of(evaluator, data_type, result, capped=capped)


@router.get(
    "/evaluator-agreement",
    response_model=AgreementSummaryList,
    summary="List agreement for every LLM judge",
)
async def list_agreement(
    project_id: uuid.UUID, access: AgreementReader, db: DbSession
) -> AgreementSummaryList:
    """One row per LLM-judge evaluator, by name; heuristic and webhook evaluators have none."""
    project = access.require_project()
    judges = await agreement_queries.judges(db, project.id)
    measured = await _measure(db, project.id, judges)
    return AgreementSummaryList(
        items=[AgreementSummaryOut.of(judge, measured[judge.id][1]) for judge in judges]
    )

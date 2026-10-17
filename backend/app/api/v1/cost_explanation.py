"""The lines behind one span's stored cost (spec §8.1).

`GET /projects/{project_id}/traces/{trace_id}/spans/{span_id}/cost-explanation` explains the
figure ingestion stored from the price row that produced it, so drift shows up instead of being
re-priced away. Filed under the `traces` tag with the rest of the trace explorer.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DbSession, ReadAccess, require
from app.api.schemas import CostExplanationOut
from app.core.errors import not_found
from app.core.observability import feature_used
from app.core.permissions import Permission
from app.pricing.explain_service import explain_stored_span

router = APIRouter(prefix="/projects/{project_id}", tags=["traces"])

# Also readable by an API key with `traces:read`, for the project it belongs to.
ProjectReader = Annotated[ReadAccess, Depends(require(Permission.PROJECT_READ, allow_api_key=True))]


@router.get(
    "/traces/{trace_id}/spans/{span_id}/cost-explanation",
    response_model=CostExplanationOut,
    summary="Explain a span's cost",
)
async def get_cost_explanation(
    project_id: uuid.UUID, trace_id: str, span_id: str, access: ProjectReader, db: DbSession
) -> CostExplanationOut:
    """Explains a span's stored cost, one line per token class and fee.

    Each line has its quantity, rate, tier factor and amount, and the recomputed total is shown
    beside the stored one with a status that compares them. The figure is rebuilt from the price
    row the span was priced with, never from today's prices. `404 NOT_FOUND` when the span is not
    visible in the project.
    """
    project = access.require_project()
    explanation = await explain_stored_span(
        db, project.id, trace_id, span_id, org_id=project.org_id
    )
    if explanation is None:
        raise not_found()
    feature_used("cost_explanation")
    return explanation

"""The price catalogue, and the models a project used that no price covered.

Read-only: prices come from the bundled snapshot (`spanlight sync-prices`), not from the API.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, ReadAccess, require
from app.api.params import Environment
from app.api.schemas import PriceOut, UnpricedModelOut
from app.api.window import Window
from app.core.permissions import Permission
from app.pricing.queries import list_prices, list_unpriced_models

router = APIRouter(tags=["prices"])

# Also readable by an API key with `traces:read`, for the project it belongs to.
ProjectReader = Annotated[ReadAccess, Depends(require(Permission.PROJECT_READ, allow_api_key=True))]


@router.get("/prices", response_model=list[PriceOut])
async def get_prices(auth: CurrentUser, db: DbSession) -> list[PriceOut]:
    """The price table the server computes costs from. Any signed-in user, session or token."""
    return [PriceOut.model_validate(price) for price in await list_prices(db)]


@router.get("/projects/{project_id}/unpriced-models", response_model=list[UnpricedModelOut])
async def get_unpriced_models(
    project_id: uuid.UUID,
    access: ProjectReader,
    db: DbSession,
    window: Window,
    environment: Environment = None,
) -> list[UnpricedModelOut]:
    """Models with LLM usage in the window whose calls got no cost for want of a price."""
    project = access.require_project()
    models = await list_unpriced_models(
        db, project.id, start=window.start, end=window.end, environment=environment
    )
    return [
        UnpricedModelOut(
            provider=model.provider,
            model=model.model,
            llm_calls=model.llm_calls,
            input_tokens=model.input_tokens,
            output_tokens=model.output_tokens,
        )
        for model in models
    ]

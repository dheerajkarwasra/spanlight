"""A prompt version by its id: what an evaluator or an experiment config names.

Members read it (`project:read`). A version of another project's prompt is `404`.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import Access, DbSession, require
from app.api.schemas.prompt_versions import PromptVersionByIdOut
from app.core.errors import not_found
from app.core.permissions import Permission
from app.prompts import queries

router = APIRouter(prefix="/projects/{project_id}/prompt-versions", tags=["prompts"])

PromptReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]


@router.get(
    "/{version_id}", response_model=PromptVersionByIdOut, summary="Get a prompt version by id"
)
async def get_prompt_version(
    project_id: uuid.UUID, version_id: uuid.UUID, access: PromptReader, db: DbSession
) -> PromptVersionByIdOut:
    """The version with its prompt's name; `model_config` is the version's model advice."""
    found = await queries.version_with_prompt_name(db, access.require_project().id, version_id)
    if found is None:
        raise not_found()
    prompt_name, version = found
    return PromptVersionByIdOut.of(prompt_name, version)

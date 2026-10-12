"""How much a prompt version was used: `GET /prompts/{name}/versions/{version}/usage`.

Split from `app.api.v1.prompts` so that module stays small. Mounted before it in
`DOMAIN_ROUTERS`, so the more specific path wins over the catch-all `{name:path}` routes.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import Access, ClockDep, DbSession, require
from app.api.schemas import VersionUsageOut
from app.api.v1.prompts import VersionNumber, WireName
from app.core.errors import not_found
from app.core.permissions import Permission
from app.core.wire import unescape_path_name
from app.prompts import queries
from app.prompts.usage import version_usage

router = APIRouter(prefix="/projects/{project_id}/prompts", tags=["prompts"])

PromptReader = Annotated[Access, Depends(require(Permission.PROJECT_READ))]


@router.get(
    "/{name:path}/versions/{version}/usage",
    response_model=VersionUsageOut,
    summary="Get a prompt version's usage",
)
async def get_version_usage(
    project_id: uuid.UUID,
    name: WireName,
    version: VersionNumber,
    access: PromptReader,
    db: DbSession,
    clock: ClockDep,
) -> VersionUsageOut:
    """Traces, LLM calls, cost and p95 latency of the calls that used the version, last 7 days."""
    project = access.require_project()
    prompt = await queries.get_prompt(db, project.id, unescape_path_name(name))
    found = await queries.get_version(db, prompt.id, version) if prompt else None
    if found is None:
        raise not_found()
    return VersionUsageOut.of(await version_usage(db, project.id, found.id, clock()))

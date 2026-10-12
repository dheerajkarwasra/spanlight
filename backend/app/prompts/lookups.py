"""Prompt reads for other domains (the LLM judges' prompts), so they never reach into
`app.prompts.queries`. `db` is bound to the project."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PromptVersion
from app.prompts import queries


async def prompt_exists(db: AsyncSession, project_id: uuid.UUID, name: str) -> bool:
    return await queries.get_prompt(db, project_id, name) is not None


async def latest_version_named(
    db: AsyncSession, project_id: uuid.UUID, name: str
) -> PromptVersion | None:
    """The highest version of the project's prompt `name`; None when there is no such prompt."""
    prompt = await queries.get_prompt(db, project_id, name)
    return None if prompt is None else await queries.latest_version(db, prompt.id)

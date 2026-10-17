"""The failure category: an ordinary categorical score named `failure_category`.

Nothing new is stored for it; the convention is the name. Its config is created with the list a
reviewer sends (the dashboard pre-fills `DEFAULT_CATEGORIES` as a prompt to edit, not a
taxonomy) and edited afterwards with the usual config `PATCH`. `none` is required because a
category's rate is over the traces a person reviewed, and a reviewed trace that passed must be
labelable.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AnnotationQueue, ScoreConfig
from app.scores import configs, queries
from app.scores.schemas import Category, ScoreConfigSpec
from app.scores.types import ScoreDataType

FAILURE_CATEGORY_SCORE = "failure_category"
NONE_CATEGORY = "none"
DEFAULT_CATEGORIES: tuple[str, ...] = (
    "none",
    "wrong_answer",
    "hallucination",
    "ignored_instructions",
    "format",
    "refusal",
    "tool_misuse",
    "context_loss",
    "truncated",
    "too_slow",
    "other",
)


class FailureCategoriesIn(BaseModel):
    """The categories of a new failure-category config, in the order the dashboard shows them."""

    model_config = ConfigDict(extra="forbid")

    categories: list[Category] = Field(min_length=2, max_length=30)

    @field_validator("categories")
    @classmethod
    def _distinct_with_none(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("categories must be distinct")
        if NONE_CATEGORY not in value:
            raise ValueError(f"categories must include {NONE_CATEGORY!r}")
        return value


async def create_failure_category_config(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    body: FailureCategoriesIn,
    *,
    now: datetime,
    ip: str | None = None,
) -> ScoreConfig:
    """Store the explicit categorical config. Raises `ScoreConfigNameTakenError` when the
    project already has a `failure_category` config (edit that one instead) and
    `ScoreConfigLimitError` at the project's config limit."""
    spec = ScoreConfigSpec(
        name=FAILURE_CATEGORY_SCORE,
        data_type=ScoreDataType.CATEGORICAL,
        categories=body.categories,
    )
    return await configs.create_config(db, org_id, project_id, actor_id, spec, now=now, ip=ip)


async def failure_category_config(db: AsyncSession, project_id: uuid.UUID) -> ScoreConfig | None:
    """The project's `failure_category` config, or None when it has none."""
    found = await queries.configs_by_name(db, project_id, [FAILURE_CATEGORY_SCORE])
    return found.get(FAILURE_CATEGORY_SCORE)


def queue_asks_failure_category(queue: AnnotationQueue, config: ScoreConfig | None) -> bool:
    """Whether the queue lists the failure-category config. A config of that name that is not
    categorical (an implicit one a numeric score created) does not count."""
    return (
        config is not None
        and config.data_type == ScoreDataType.CATEGORICAL
        and config.id in queue.score_config_ids
    )

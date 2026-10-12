"""From a config a request sends to the config an evaluator stores.

A judge may name one of the built-in judge templates instead of a prompt version; the template is
materialized as the project's prompt `judges/<name>` (version 1, created on first use) and the
stored config names that version, so a stored judge is reproducible like any other. A PATCH sends
only the config fields to change; they are merged into the stored config first, and a
`judge_template` there replaces the stored `prompt_version_id`.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Evaluator
from app.evals.config import AnyConfigIn, JudgeConfigIn, parse_config_in
from app.evals.errors import EvaluatorConfigError
from app.evals.judge_templates import materialize_template
from app.evals.validation import AnyConfig


async def resolve_config(
    db: AsyncSession,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    actor_id: uuid.UUID,
    config: AnyConfigIn,
    *,
    now: datetime,
) -> AnyConfig:
    """The config to store. A judge template becomes its prompt version (created when missing;
    `db` is bound to the project and the caller commits)."""
    if not isinstance(config, JudgeConfigIn):
        return config
    if config.judge_template is not None:
        version = await materialize_template(
            db, org_id, project_id, actor_id, config.judge_template, now=now
        )
        return config.stored(version.id)
    if config.prompt_version_id is None:  # excluded by the model's validator
        raise EvaluatorConfigError(
            "VALIDATION_ERROR",
            "Give exactly one of prompt_version_id and judge_template.",
            "config",
        )
    return config.stored(config.prompt_version_id)


def patched_config_in(evaluator: Evaluator, changes: dict[str, Any] | None) -> AnyConfigIn:
    """The stored config with `changes` merged in; `kind` cannot change. Raises
    `EvaluatorConfigError` for a merged config that does not validate."""
    if not changes:
        return _parsed(evaluator.config)
    if changes.get("kind", evaluator.kind) != evaluator.kind:
        raise EvaluatorConfigError(
            "VALIDATION_ERROR",
            "An evaluator keeps its kind; create another evaluator instead.",
            "config.kind",
        )
    merged = {**evaluator.config, **changes}
    if "judge_template" in changes and "prompt_version_id" not in changes:
        merged.pop("prompt_version_id", None)
    return _parsed(merged)


def _parsed(raw: dict[str, Any]) -> AnyConfigIn:
    try:
        return parse_config_in(raw)
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in ("config", *first["loc"]))
        raise EvaluatorConfigError("VALIDATION_ERROR", first["msg"], where) from None

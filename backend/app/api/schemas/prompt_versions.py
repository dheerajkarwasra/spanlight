"""One prompt version read by its id, as an evaluator or experiment names it."""

from datetime import datetime
from typing import Annotated, Any

from pydantic import Field

from app.api.schemas.common import ApiModel
from app.db.models import PromptKind, PromptVersion


class PromptVersionByIdOut(ApiModel):
    """The version with its prompt's name. The model advice is `model_config` on the wire and
    `advice` in code, because pydantic reserves the name."""

    prompt_name: str
    version: int
    kind: PromptKind
    template: dict[str, Any]
    variables: list[str]
    advice: Annotated[dict[str, Any], Field(serialization_alias="model_config")]
    created_at: datetime

    @classmethod
    def of(cls, prompt_name: str, version: PromptVersion) -> "PromptVersionByIdOut":
        return cls(
            prompt_name=prompt_name,
            version=version.version,
            kind=version.kind,
            template=version.template,
            variables=list(version.variables),
            advice=version.model_config,
            created_at=version.created_at,
        )

"""Prompts, their versions and labels.

The create bodies are owned by the prompts domain (`app.prompts.schemas`), which validates them;
they are re-exported here so routers keep importing from `app.api.schemas`. The wire name of a
version's model advice is `model_config`; in code it is `advice`, because pydantic reserves the
other name.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import Field

from app.api.schemas.common import ApiModel, Money
from app.db.models import Prompt, PromptKind, PromptVersion
from app.prompts.queries import LabelRow, PromptRow
from app.prompts.schemas import PromptCreate, VersionCreate
from app.prompts.usage import VersionUsage

__all__ = [
    "LabelOut",
    "LabelSetIn",
    "PromptCreate",
    "PromptListItemOut",
    "PromptOut",
    "VersionCreate",
    "VersionOut",
    "VersionUsageOut",
]


class LabelOut(ApiModel):
    label: str
    version: int

    @classmethod
    def of(cls, row: LabelRow) -> "LabelOut":
        return cls(label=row.label, version=row.version)


class LabelSetIn(ApiModel):
    version: Annotated[int, Field(ge=1)]


class VersionUsageOut(ApiModel):
    """Over the last 7 days. `cost_usd` and `p95_ms` are null when unknown, never 0.

    `cost_usd` sums the calls that have a cost; when `has_unpriced` is true some LLM calls had
    none (unknown model or missing usage), so it is a lower bound.
    """

    traces: int
    llm_calls: int
    cost_usd: Money | None
    p95_ms: float | None
    has_unpriced: bool

    @classmethod
    def of(cls, usage: VersionUsage) -> "VersionUsageOut":
        return cls.model_validate(usage)


class VersionOut(ApiModel):
    id: uuid.UUID
    version: int
    kind: PromptKind
    # `{"kind": "text", "text": ...}` or `{"kind": "chat", "messages": [{"role", "content"}]}`.
    template: dict[str, Any]
    # Names the template uses, in order of first appearance; filled by the server.
    variables: list[str]
    # Advice for clients: `model`, `temperature`, `max_tokens`, `top_p`; absent keys are unset.
    advice: Annotated[dict[str, Any], Field(serialization_alias="model_config")]
    commit_message: str | None
    created_by: uuid.UUID | None
    created_at: datetime
    usage_7d: VersionUsageOut | None = Field(
        default=None,
        description="Only filled by the versions list with `usage=true`; null otherwise.",
    )

    @classmethod
    def of(cls, version: PromptVersion) -> "VersionOut":
        return cls(
            id=version.id,
            version=version.version,
            kind=version.kind,
            template=version.template,
            variables=list(version.variables),
            advice=version.model_config,
            commit_message=version.commit_message,
            created_by=version.created_by,
            created_at=version.created_at,
        )


class PromptListItemOut(ApiModel):
    id: uuid.UUID
    name: str
    description: str | None
    kind: PromptKind
    latest_version: int
    labels: list[LabelOut]
    updated_at: datetime

    @classmethod
    def of(cls, row: PromptRow, labels: list[LabelRow]) -> "PromptListItemOut":
        prompt = row.prompt
        return cls(
            id=prompt.id,
            name=prompt.name,
            description=prompt.description,
            kind=row.kind,
            latest_version=row.latest_version,
            labels=[LabelOut.of(label) for label in labels],
            updated_at=prompt.updated_at,
        )


class PromptOut(ApiModel):
    id: uuid.UUID
    name: str
    description: str | None
    kind: PromptKind
    created_at: datetime
    updated_at: datetime
    latest: VersionOut
    labels: list[LabelOut]

    @classmethod
    def of(cls, prompt: Prompt, latest: PromptVersion, labels: list[LabelRow]) -> "PromptOut":
        return cls(
            id=prompt.id,
            name=prompt.name,
            description=prompt.description,
            kind=latest.kind,
            created_at=prompt.created_at,
            updated_at=prompt.updated_at,
            latest=VersionOut.of(latest),
            labels=[LabelOut.of(label) for label in labels],
        )

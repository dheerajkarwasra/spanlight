"""What a client sends to create a prompt or a version, and the conversion to the pure template
types in `app.prompts.templates`."""

import json
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.prompts.templates import MAX_CHAT_MESSAGES, MAX_TEMPLATE_BYTES, Message, Template

MAX_MODEL_CONFIG_BYTES = 2048
MAX_COMMIT_MESSAGE = 500
MAX_DESCRIPTION = 1000

# Path segments the routes use after a prompt name; a name with one would be ambiguous in a URL.
RESERVED_SEGMENTS = frozenset({"versions", "labels"})


def _no_reserved_segment(name: str) -> str:
    if RESERVED_SEGMENTS & set(name.split("/")):
        raise ValueError("a name cannot have a path segment named 'versions' or 'labels'")
    return name


# The database CHECKs repeat the patterns.
PromptName = Annotated[
    str,
    StringConstraints(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*(/[A-Za-z0-9][A-Za-z0-9_.-]*)*$", max_length=128
    ),
    AfterValidator(_no_reserved_segment),
]
LabelName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$", max_length=32)]
CommitMessage = Annotated[str, StringConstraints(max_length=MAX_COMMIT_MESSAGE)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelAdvice(_Strict):
    """Free-form advice for clients on how to run the prompt; the server never uses it."""

    model: Annotated[str, StringConstraints(min_length=1, max_length=200)] | None = None
    temperature: Annotated[float, Field(ge=0, le=2)] | None = None
    max_tokens: Annotated[int, Field(ge=1, le=10_000_000)] | None = None
    top_p: Annotated[float, Field(ge=0, le=1)] | None = None

    def stored(self) -> dict[str, object]:
        return self.model_dump(exclude_none=True)


class ChatMessageIn(_Strict):
    role: Literal["system", "user", "assistant"]
    content: Annotated[str, StringConstraints(max_length=MAX_TEMPLATE_BYTES)]


class TextTemplateIn(_Strict):
    kind: Literal["text"]
    text: Annotated[str, StringConstraints(max_length=MAX_TEMPLATE_BYTES)]


class ChatTemplateIn(_Strict):
    kind: Literal["chat"]
    messages: Annotated[list[ChatMessageIn], Field(min_length=1, max_length=MAX_CHAT_MESSAGES)]


TemplateIn = Annotated[TextTemplateIn | ChatTemplateIn, Field(discriminator="kind")]


def to_template(template: TextTemplateIn | ChatTemplateIn) -> Template:
    """The pure template for `extract_variables`."""
    if isinstance(template, TextTemplateIn):
        return Template("text", text=template.text)
    return Template("chat", messages=[Message(m.role, m.content) for m in template.messages])


class VersionBody(_Strict):
    template: TemplateIn
    advice: ModelAdvice = Field(default_factory=ModelAdvice, alias="model_config")
    commit_message: CommitMessage | None = None

    @field_validator("advice")
    @classmethod
    def _small(cls, value: ModelAdvice) -> ModelAdvice:
        if len(json.dumps(value.stored())) > MAX_MODEL_CONFIG_BYTES:
            raise ValueError(f"must be at most {MAX_MODEL_CONFIG_BYTES} bytes")
        return value


class VersionCreate(VersionBody):
    """The body of `POST /prompts/{name}/versions`."""


class PromptCreate(VersionBody):
    """The body of `POST /prompts`: the prompt and its version 1."""

    name: PromptName
    # Repeats `template.kind`, so a client states what it is creating; the two must agree.
    kind: Literal["text", "chat"]
    description: Annotated[str, StringConstraints(max_length=MAX_DESCRIPTION)] | None = None

    @model_validator(mode="after")
    def _kind_matches_template(self) -> Self:
        if self.kind != self.template.kind:
            raise ValueError("kind must equal template.kind")
        return self

"""What a client sends to the playground, and what one configuration's run comes back as."""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.gateway.sse import Usage
from app.prompts.schemas import ChatMessageIn
from app.prompts.templates import MAX_CHAT_MESSAGES, MAX_TEMPLATE_BYTES

MAX_CONFIGS = 3
MAX_OUTPUT_TOKENS = 4096
MAX_VARIABLES = 100


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Params(_Strict):
    """Sampling parameters; an unset one is left to the provider's default."""

    temperature: Annotated[float, Field(ge=0, le=2)] | None = None
    max_tokens: Annotated[int, Field(ge=1, le=MAX_OUTPUT_TOKENS)] | None = None
    top_p: Annotated[float, Field(ge=0, le=1)] | None = None


class PromptSource(_Strict):
    """A stored prompt version with the values of its variables."""

    prompt_version_id: uuid.UUID
    variables: Annotated[
        dict[
            Annotated[str, StringConstraints(max_length=64)],
            Annotated[str, StringConstraints(max_length=MAX_TEMPLATE_BYTES)],
        ],
        Field(max_length=MAX_VARIABLES),
    ] = Field(default_factory=dict)


class MessagesSource(_Strict):
    """Messages typed in the playground, sent as they are."""

    messages: Annotated[list[ChatMessageIn], Field(min_length=1, max_length=MAX_CHAT_MESSAGES)]


class PlaygroundConfig(_Strict):
    """One run: a credential of the organization, a model, parameters and what to send."""

    credential_id: uuid.UUID
    model: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    params: Params = Field(default_factory=Params)
    source: PromptSource | MessagesSource


class PlaygroundRunIn(_Strict):
    """The body of `POST /playground/runs`."""

    configs: Annotated[list[PlaygroundConfig], Field(min_length=1, max_length=MAX_CONFIGS)]


@dataclass(frozen=True)
class PlaygroundResult:
    """How one configuration went. `error` set means there is no `output`.

    `ttft_ms` is always None: responses are not streamed. `cost_usd` is None when the model has
    no price or the provider reported no usage; unknown is never 0.
    """

    output: str | None
    usage: Usage | None
    cost_usd: Decimal | None
    latency_ms: float | None
    ttft_ms: float | None
    trace_id: str | None
    span_id: str | None
    error: str | None

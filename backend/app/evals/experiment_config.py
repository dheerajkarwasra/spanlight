"""What an experiment is configured with, stored in `experiments.config`.

The target is discriminated on `kind`:

* `prompt_version` names a prompt version of the project; each dataset item fills its variables;
* `messages` sends typed messages as they are, the same for every item.

Both name an organization credential and a model; the call goes through the gateway on a route
built in memory. `max_cost_usd` caps what one run may spend on its target calls (the attached
evaluators spend under their own monthly budgets) and `max_items` how many dataset items it reads,
up to the `EXPERIMENT_MAX_ITEMS` setting. These models validate shape only; the service checks
every id against the project and the organization.
"""

import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

from app.playground.schemas import Params
from app.prompts.schemas import ChatMessageIn
from app.prompts.templates import MAX_CHAT_MESSAGES

DEFAULT_MAX_COST_USD = Decimal(5)
MAX_COST_CEILING_USD = Decimal(1000)
DEFAULT_MAX_ITEMS = 1000
MAX_EVALUATORS = 20

ExperimentName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
Model = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PromptVersionTarget(_Config):
    """A stored prompt version; each item's input fills its variables."""

    kind: Literal["prompt_version"]
    prompt_version_id: uuid.UUID
    credential_id: uuid.UUID
    model: Model
    params: Params = Field(default_factory=Params)


class AdHocTarget(_Config):
    """Typed messages, sent the same for every item (the item's input is not used)."""

    kind: Literal["messages"]
    credential_id: uuid.UUID
    model: Model
    messages: Annotated[list[ChatMessageIn], Field(min_length=1, max_length=MAX_CHAT_MESSAGES)]
    params: Params = Field(default_factory=Params)


Target = Annotated[PromptVersionTarget | AdHocTarget, Field(discriminator="kind")]
_TARGET: TypeAdapter[PromptVersionTarget | AdHocTarget] = TypeAdapter(Target)


def parse_target(stored: Any) -> PromptVersionTarget | AdHocTarget:
    """A stored target (`experiment_runs.target`). Raises `ValidationError`."""
    return _TARGET.validate_python(stored)


class ExperimentConfig(_Config):
    target: Target
    max_cost_usd: Annotated[Decimal, Field(gt=0, le=MAX_COST_CEILING_USD, decimal_places=4)] = (
        DEFAULT_MAX_COST_USD
    )
    max_items: Annotated[int, Field(ge=1)] = DEFAULT_MAX_ITEMS
    # A dataset item metadata key whose value groups items that are not independent (one source
    # document, conversation or user): intervals and the paired test are then clustered on it.
    # An item without the key is a cluster of its own. Configs stored before it read as None.
    cluster_key: Annotated[str, StringConstraints(min_length=1, max_length=100)] | None = None


class TargetOverride(_Config):
    """Run a prompt-version experiment with another version of the same prompt; the credential
    and model stay the experiment's."""

    prompt_version_id: uuid.UUID


class RunStart(BaseModel):
    """The optional body that starts a run."""

    model_config = ConfigDict(extra="forbid")

    target_override: TargetOverride | None = None


class ExperimentSpec(BaseModel):
    """The body that creates an experiment."""

    model_config = ConfigDict(extra="forbid")

    name: ExperimentName
    dataset_id: uuid.UUID
    config: ExperimentConfig
    evaluator_ids: Annotated[list[uuid.UUID], Field(max_length=MAX_EVALUATORS)] = Field(
        default_factory=list
    )

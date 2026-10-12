"""What an evaluator is configured with: its kind-specific config and the traces it samples.

`EvaluatorConfig` is stored in `evaluators.config` and is discriminated on `kind`, which always
equals the `kind` column:

* `heuristic` wraps one rule of `app.evals.evaluators.heuristic` in `check`;
* `llm_judge` names a chat prompt version, an organization credential (Anthropic or OpenAI) and a
  model; the call goes through the gateway on a route built in memory, under the evaluator's
  monthly budget;
* `webhook` names an organization alert channel of kind `webhook`.

These models validate shape only; the service checks every id against the project and the
organization.
"""

import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

from app.evals.evaluators.heuristic import ExactMatchRule, HeuristicConfig, LevenshteinRule
from app.scores.schemas import NAME_PATTERN

MAX_JUDGE_TOKENS = 4096
MAX_FILTER_VALUES = 50

FilterValue = Annotated[str, Field(min_length=1, max_length=200)]


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HeuristicEvaluatorConfig(_Config):
    kind: Literal["heuristic"]
    check: HeuristicConfig

    @property
    def needs_expected_output(self) -> bool:
        """Whether the rule compares against an expected output, which online traces lack."""
        return isinstance(self.check, ExactMatchRule | LevenshteinRule)


class JudgeConfig(_Config):
    kind: Literal["llm_judge"]
    prompt_version_id: uuid.UUID
    credential_id: uuid.UUID
    model: str = Field(min_length=1, max_length=200)
    max_tokens: int = Field(default=256, ge=1, le=MAX_JUDGE_TOKENS)
    # None (the default) sends no `temperature`: the newest Claude models reject non-default
    # sampling parameters with a 400.
    temperature: Decimal | None = Field(default=None, ge=0, le=2, decimal_places=2)


JudgeTemplateName = Literal["relevance", "faithfulness", "toxicity", "format_compliance"]


class JudgeConfigIn(_Config):
    """A judge config as a request sends it: a prompt version of the project, or one of the
    built-in judge templates (`app.evals.judge_templates`), exactly one of them. The service
    turns a template into the project's prompt `judges/<name>` version 1 and stores the version
    id, so a stored judge always names a prompt version (`JudgeConfig`)."""

    kind: Literal["llm_judge"]
    prompt_version_id: uuid.UUID | None = None
    judge_template: JudgeTemplateName | None = None
    credential_id: uuid.UUID
    model: str = Field(min_length=1, max_length=200)
    max_tokens: int = Field(default=256, ge=1, le=MAX_JUDGE_TOKENS)
    temperature: Decimal | None = Field(default=None, ge=0, le=2, decimal_places=2)

    @model_validator(mode="after")
    def _one_prompt(self) -> "JudgeConfigIn":
        if (self.prompt_version_id is None) == (self.judge_template is None):
            raise ValueError("Give exactly one of prompt_version_id and judge_template.")
        return self

    def stored(self, prompt_version_id: uuid.UUID) -> JudgeConfig:
        """The config to store, naming `prompt_version_id`."""
        fields = self.model_dump(exclude={"prompt_version_id", "judge_template"})
        return JudgeConfig(**fields, prompt_version_id=prompt_version_id)


class WebhookEvaluatorConfig(_Config):
    kind: Literal["webhook"]
    channel_id: uuid.UUID


EvaluatorConfig = Annotated[
    HeuristicEvaluatorConfig | JudgeConfig | WebhookEvaluatorConfig, Field(discriminator="kind")
]
EvaluatorConfigIn = Annotated[
    HeuristicEvaluatorConfig | JudgeConfigIn | WebhookEvaluatorConfig, Field(discriminator="kind")
]
AnyConfigIn = HeuristicEvaluatorConfig | JudgeConfigIn | WebhookEvaluatorConfig


class EvaluatorFilters(_Config):
    """Which traces online sampling considers. Empty lists match everything."""

    environments: list[FilterValue] = Field(default_factory=list, max_length=MAX_FILTER_VALUES)
    models: list[FilterValue] = Field(default_factory=list, max_length=MAX_FILTER_VALUES)
    tags: list[FilterValue] = Field(default_factory=list, max_length=MAX_FILTER_VALUES)
    trace_names: list[FilterValue] = Field(default_factory=list, max_length=MAX_FILTER_VALUES)
    min_duration_ms: int | None = Field(default=None, ge=0)


SamplingRate = Annotated[Decimal, Field(ge=0, le=1, max_digits=5, decimal_places=4)]
Budget = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=4)]
# The evaluator's scores carry its name, so the name follows the score name rule.
EvaluatorName = Annotated[str, StringConstraints(pattern=NAME_PATTERN)]


class EvaluatorSpec(BaseModel):
    """A whole evaluator: the body of a create."""

    model_config = ConfigDict(extra="forbid")

    name: EvaluatorName
    config: EvaluatorConfigIn
    sampling_rate: SamplingRate = Decimal(0)
    filters: EvaluatorFilters = Field(default_factory=EvaluatorFilters)
    monthly_budget_usd: Budget | None = None
    enabled: bool = True


class EvaluatorPatch(BaseModel):
    """The members to change; absent ones stay. `config` holds only the config fields to change
    and may not change `kind`; a judge's `judge_template` replaces its `prompt_version_id`.
    `monthly_budget_usd: null` clears the budget (refused for judges).
    """

    model_config = ConfigDict(extra="forbid")

    name: EvaluatorName | None = None
    enabled: bool | None = None
    sampling_rate: SamplingRate | None = None
    filters: EvaluatorFilters | None = None
    monthly_budget_usd: Budget | None = None
    config: dict[str, Any] | None = None


_CONFIG_ADAPTER: TypeAdapter[HeuristicEvaluatorConfig | JudgeConfig | WebhookEvaluatorConfig] = (
    TypeAdapter(EvaluatorConfig)
)


_CONFIG_IN_ADAPTER: TypeAdapter[AnyConfigIn] = TypeAdapter(EvaluatorConfigIn)


def parse_config(raw: Any) -> HeuristicEvaluatorConfig | JudgeConfig | WebhookEvaluatorConfig:
    """A config from its stored JSON. Raises pydantic's `ValidationError`."""
    return _CONFIG_ADAPTER.validate_python(raw)


def parse_config_in(raw: Any) -> AnyConfigIn:
    """A config as a request may send it (a judge may name a template). Raises pydantic's
    `ValidationError`."""
    return _CONFIG_IN_ADAPTER.validate_python(raw)

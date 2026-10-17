"""What an alert rule is created, edited and previewed from. Pure: no database, no I/O.

`RuleDefinition` is what a rule measures and when it breaches: the body of the preview.
`RuleSpec` adds the name and the channels: the body of a create and of an edit, and the shape the
evaluation job reads a rule as. Budget rules are not written through these models; their budget
owns them.

The kind decides which fields apply. A `threshold` rule needs `threshold` and refuses
`baseline_windows` and `sensitivity`; an `anomaly` rule needs `baseline_windows`, takes
`sensitivity` (default 3.0) and refuses `threshold`. Errors name the field they are about. The
bounds match migration 0303 and the phase's limits. Unknown fields are rejected.

The `error_class` and `rate_limit_kind` filters narrow the failed calls a rule counts, so they
apply to `error_rate` and `error_calls` only; each takes one value of its vocabulary
(`app.ingest.error_class`, `app.ingest.rate_limit_kind`).
"""

import uuid
from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.alerts.types import AlertRuleKind, Comparator, Metric
from app.ingest.error_class import ErrorClass
from app.ingest.rate_limit_kind import RateLimitKind

MAX_CHANNELS_PER_RULE = 10
DEFAULT_SENSITIVITY = Decimal("3.0")
DEFAULT_COOLDOWN_MINUTES = 15

RuleName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
FilterKey = Literal["environment", "provider", "model", "error_class", "rate_limit_kind"]
FilterValue = Annotated[str, StringConstraints(min_length=1, max_length=200)]
UserRuleKind = Literal["threshold", "anomaly"]

# The filters that narrow failed calls, with the values each accepts, and the metrics they apply to.
_ERROR_FILTER_VALUES: dict[FilterKey, tuple[str, ...]] = {
    "error_class": tuple(ErrorClass),
    "rate_limit_kind": tuple(RateLimitKind),
}
_ERROR_METRICS = (Metric.ERROR_RATE, Metric.ERROR_CALLS)


def _unique(ids: list[uuid.UUID]) -> list[uuid.UUID]:
    """Without repeats, in the order given."""
    return list(dict.fromkeys(ids))


ChannelIds = Annotated[
    list[uuid.UUID], Field(max_length=MAX_CHANNELS_PER_RULE), AfterValidator(_unique)
]


def _kind_of(info: ValidationInfo) -> AlertRuleKind | None:
    """The kind sent with the field, or None when the kind itself failed validation."""
    kind = info.data.get("kind")
    return AlertRuleKind(kind) if kind is not None else None


class RuleDefinition(BaseModel):
    """What a rule measures and when it breaches. The preview's body."""

    model_config = ConfigDict(extra="forbid")

    # Declared first: the field validators below read it.
    kind: UserRuleKind
    metric: Metric
    comparator: Comparator
    threshold: Annotated[Decimal, Field(ge=0, max_digits=21, decimal_places=6)] | None = Field(
        default=None, validate_default=True
    )
    window_minutes: Annotated[int, Field(ge=5, le=1440)]
    baseline_windows: Annotated[int, Field(ge=3, le=30)] | None = Field(
        default=None, validate_default=True
    )
    sensitivity: Annotated[Decimal, Field(ge=Decimal("0.5"), le=5, decimal_places=2)] | None = (
        Field(default=None, validate_default=True)
    )
    filters: Annotated[dict[FilterKey, FilterValue], Field(default_factory=dict)]
    cooldown_minutes: Annotated[int, Field(ge=0, le=1440)] = DEFAULT_COOLDOWN_MINUTES
    enabled: bool = True

    @property
    def rule_kind(self) -> AlertRuleKind:
        return AlertRuleKind(self.kind)

    @field_validator("metric")
    @classmethod
    def _not_spend(cls, value: Metric) -> Metric:
        if value is Metric.SPEND:
            raise ValueError("spend is measured by budgets; use cost_usd for a rule")
        return value

    @field_validator("filters")
    @classmethod
    def _known_error_values(cls, value: dict[FilterKey, str]) -> dict[FilterKey, str]:
        for key, allowed in _ERROR_FILTER_VALUES.items():
            given = value.get(key)
            if given is not None and given not in allowed:
                raise ValueError(f"{key}: must be one of {', '.join(allowed)}")
        return value

    @model_validator(mode="after")
    def _error_filters_for_metric(self) -> Self:
        narrowing = [key for key in _ERROR_FILTER_VALUES if key in self.filters]
        if narrowing and self.metric not in _ERROR_METRICS:
            raise ValueError(f"filters.{narrowing[0]}: applies to error_rate and error_calls")
        return self

    @field_validator("threshold")
    @classmethod
    def _threshold_for_kind(cls, value: Decimal | None, info: ValidationInfo) -> Decimal | None:
        kind = _kind_of(info)
        if kind is AlertRuleKind.THRESHOLD and value is None:
            raise ValueError("is required for a threshold rule")
        if kind is AlertRuleKind.ANOMALY and value is not None:
            raise ValueError("is not accepted for an anomaly rule: the baseline sets it")
        return value

    @field_validator("baseline_windows")
    @classmethod
    def _baseline_for_kind(cls, value: int | None, info: ValidationInfo) -> int | None:
        kind = _kind_of(info)
        if kind is AlertRuleKind.ANOMALY and value is None:
            raise ValueError("is required for an anomaly rule")
        if kind is AlertRuleKind.THRESHOLD and value is not None:
            raise ValueError("applies to anomaly rules only")
        return value

    @field_validator("sensitivity")
    @classmethod
    def _sensitivity_for_kind(cls, value: Decimal | None, info: ValidationInfo) -> Decimal | None:
        kind = _kind_of(info)
        if kind is AlertRuleKind.THRESHOLD and value is not None:
            raise ValueError("applies to anomaly rules only")
        if kind is AlertRuleKind.ANOMALY and value is None:
            return DEFAULT_SENSITIVITY
        return value

    def column_values(self) -> dict[str, Any]:
        """The `alert_rules` columns this definition sets (everything but the name and channels)."""
        return {
            "kind": self.rule_kind,
            "metric": self.metric,
            "comparator": self.comparator,
            "threshold": self.threshold,
            "window_minutes": self.window_minutes,
            "baseline_windows": self.baseline_windows,
            "sensitivity": self.sensitivity,
            "filters": dict(self.filters),
            "cooldown_minutes": self.cooldown_minutes,
            "enabled": self.enabled,
        }


class RuleSpec(RuleDefinition):
    """A whole rule as a person writes it: the body of a create and of an edit.

    An edit sends the whole rule again and replaces it; the rule's kind may change.
    """

    name: RuleName
    channel_ids: ChannelIds = Field(default_factory=list)

    def column_values(self) -> dict[str, Any]:
        return {**super().column_values(), "name": self.name, "channel_ids": self.channel_ids}

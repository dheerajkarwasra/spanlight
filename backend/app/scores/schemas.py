"""What scores and score configs are written from. Pure: no database, no I/O.

`ScoreIn` is one score as any source sends it. Its wire limits (lengths, the trace id format)
are checked here; what depends on the score's config (type, range, categories) and the name's
format are checked per score by `validation.validate_score`, so one bad score in a batch is
rejected on its own instead of failing the batch. `value` accepts JSON `true`/`false`, which
infer a boolean score and are stored as 1/0. NUL is removed from `string_value`, `comment` and
`span_id` (PostgreSQL refuses it), so it can never fail a batch's insert.

`ScoreConfigSpec` is a whole config (the body of a create); `ScoreConfigPatch` is a partial edit.
The bounds match migration 0502. Unknown fields are rejected.
"""

import uuid
from decimal import Decimal
from typing import Annotated, Any, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.core.text import clean_text
from app.ingest.schemas import TraceId
from app.scores.types import ScoreDataType

NAME_PATTERN = r"^[a-z0-9][a-z0-9_.-]{0,63}$"
MAX_CATEGORIES = 100

ScoreName = Annotated[str, StringConstraints(min_length=1, max_length=64)]
ConfigName = Annotated[str, StringConstraints(pattern=NAME_PATTERN)]


def _storable(value: object) -> object:
    """NUL removed and lone surrogates replaced before the length checks, so a score carrying
    them is stored (or rejected on its own) instead of failing its batch's insert."""
    return clean_text(value) if isinstance(value, str) else value


SpanRef = Annotated[
    str,
    BeforeValidator(_storable),
    StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=64),
]
StringValue = Annotated[
    str, BeforeValidator(_storable), StringConstraints(min_length=1, max_length=200)
]
Comment = Annotated[str, BeforeValidator(_storable), StringConstraints(max_length=2000)]
Description = Annotated[str, StringConstraints(max_length=500)]
Bound = Annotated[Decimal, Field(ge=-1_000_000_000, le=1_000_000_000, decimal_places=4)]
Category = Annotated[str, StringConstraints(min_length=1, max_length=200)]
# A JSON boolean is kept as a bool (it infers a boolean score); any other number is a Decimal.
ScoreValue = StrictBool | Decimal


class ScoreIn(BaseModel):
    """One score. `id` makes a retry idempotent; the server generates one when it is absent."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID | None = None
    trace_id: TraceId
    span_id: SpanRef | None = None
    name: ScoreName
    value: ScoreValue | None = None
    string_value: StringValue | None = None
    comment: Comment | None = None


class ScoreConfigSpec(BaseModel):
    """A whole score config: the body of a create."""

    model_config = ConfigDict(extra="forbid")

    name: ConfigName
    data_type: ScoreDataType
    min_value: Bound | None = None
    max_value: Bound | None = None
    categories: list[Category] = Field(default_factory=list, max_length=MAX_CATEGORIES)
    description: Description | None = None

    @field_validator("categories")
    @classmethod
    def _distinct(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("categories must be distinct")
        return value

    @model_validator(mode="after")
    def _bounds_for_type(self) -> Self:
        check_bounds(self.data_type, self.min_value, self.max_value, self.categories)
        return self

    def column_values(self) -> dict[str, Any]:
        """The `score_configs` columns this spec sets."""
        return {
            "name": self.name,
            "data_type": self.data_type,
            "min_value": self.min_value,
            "max_value": self.max_value,
            "categories": self.categories,
            "description": self.description,
        }


class ScoreConfigPatch(BaseModel):
    """A partial edit: the fields sent replace the config's. The name cannot change; the type
    can only while no score uses the name."""

    model_config = ConfigDict(extra="forbid")

    data_type: ScoreDataType | None = None
    min_value: Bound | None = None
    max_value: Bound | None = None
    categories: list[Category] | None = Field(default=None, max_length=MAX_CATEGORIES)
    description: Description | None = None

    @field_validator("data_type", "categories")
    @classmethod
    def _not_null(cls, value: object) -> object:
        # Runs only for fields that were sent: leaving a field out keeps it, null is refused.
        if value is None:
            raise ValueError("cannot be null; leave the field out to keep it")
        return value

    def merged_with(self, current: ScoreConfigSpec) -> ScoreConfigSpec:
        """`current` with the fields sent replaced, validated as a whole config."""
        values = current.model_dump()
        values.update(self.model_dump(exclude_unset=True))
        return ScoreConfigSpec.model_validate(values)


def check_bounds(
    data_type: ScoreDataType,
    min_value: Decimal | None,
    max_value: Decimal | None,
    categories: list[str],
) -> None:
    """A range only for numeric configs, categories only for categorical ones."""
    has_range = min_value is not None or max_value is not None
    if data_type is not ScoreDataType.NUMERIC and has_range:
        raise ValueError(f"a {data_type.value} config has no min_value or max_value")
    if data_type is not ScoreDataType.CATEGORICAL and categories:
        raise ValueError(f"a {data_type.value} config has no categories")
    if min_value is not None and max_value is not None and min_value > max_value:
        raise ValueError("min_value must not be greater than max_value")

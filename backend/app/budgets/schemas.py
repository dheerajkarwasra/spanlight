"""What a budget is created and edited from. Pure: no database, no I/O.

`BudgetSpec` is a whole budget: the body of a create. `BudgetPatch` is a partial edit: only the
fields sent change. `scope_id` goes with the scope: it is absent (or null) for a `project`
budget and required for every other scope; a gateway key id is checked against the project, and
a `metadata` or `trace_name` scope parsed, by the service, not here. The bounds match migrations
0304 and 0804 (`scope_id` up to 400 characters: a metadata key, `=` and a value is 321). Unknown
fields are rejected.
"""

from decimal import Decimal
from typing import Annotated, Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationInfo,
    field_validator,
    model_validator,
)

from app.alerts.rule_spec import ChannelIds
from app.budgets.types import BudgetAction, BudgetPeriod, BudgetScope

BudgetName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
ScopeId = Annotated[str, StringConstraints(min_length=1, max_length=400)]
AmountUsd = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=4)]


def check_scope_id(scope: BudgetScope | None, scope_id: str | None) -> str | None:
    """`scope_id` is None for a `project` budget and set for every other scope."""
    if scope is BudgetScope.PROJECT and scope_id is not None:
        raise ValueError("must be null for a project budget")
    if scope is not None and scope is not BudgetScope.PROJECT and scope_id is None:
        raise ValueError(f"is required for a {scope.value} budget")
    return scope_id


class BudgetSpec(BaseModel):
    """A whole budget: the body of a create."""

    model_config = ConfigDict(extra="forbid")

    name: BudgetName
    # Declared before `scope_id`: its validator reads it.
    scope: BudgetScope
    scope_id: ScopeId | None = Field(default=None, validate_default=True)
    period: BudgetPeriod
    amount_usd: AmountUsd
    action: BudgetAction
    enabled: bool = True
    channel_ids: ChannelIds = Field(default_factory=list)

    @field_validator("scope_id")
    @classmethod
    def _scope_id_for_scope(cls, value: str | None, info: ValidationInfo) -> str | None:
        return check_scope_id(info.data.get("scope"), value)

    def column_values(self) -> dict[str, Any]:
        """The `budgets` columns this spec sets."""
        return {
            "name": self.name,
            "scope": self.scope,
            "scope_id": self.scope_id,
            "period": self.period,
            "amount_usd": self.amount_usd,
            "action": self.action,
            "enabled": self.enabled,
            "channel_ids": self.channel_ids,
        }


class BudgetPatch(BaseModel):
    """A partial edit: the fields sent replace the budget's. Send `scope_id` with `scope`."""

    model_config = ConfigDict(extra="forbid")

    name: BudgetName | None = None
    scope: BudgetScope | None = None
    scope_id: ScopeId | None = None
    period: BudgetPeriod | None = None
    amount_usd: AmountUsd | None = None
    action: BudgetAction | None = None
    enabled: bool | None = None
    channel_ids: ChannelIds | None = None

    @field_validator("name", "scope", "period", "amount_usd", "action", "enabled", "channel_ids")
    @classmethod
    def _not_null(cls, value: object) -> object:
        # Runs only for fields that were sent: leaving a field out keeps it, null is refused.
        if value is None:
            raise ValueError("cannot be null; leave the field out to keep it")
        return value

    @model_validator(mode="after")
    def _scope_together(self) -> Self:
        if "scope_id" in self.model_fields_set and "scope" not in self.model_fields_set:
            raise ValueError("scope_id can only change together with scope")
        if "scope" in self.model_fields_set:
            check_scope_id(self.scope, self.scope_id)
        return self

    def merged_with(self, current: BudgetSpec) -> BudgetSpec:
        """`current` with the fields sent replaced; a new scope brings its own `scope_id`."""
        values = current.model_dump()
        values.update(self.model_dump(exclude_unset=True))
        if "scope" in self.model_fields_set:
            values["scope_id"] = self.scope_id
        return BudgetSpec.model_validate(values)

"""What a review policy is set from. Pure: no database, no I/O.

The bounds are the spec's: `random_rate` in [0, 0.05], `daily_cap` in [1, 500], at most 20
environments of 1-64 characters, none of them internal. A policy that would select nothing is
refused by the service (`NothingSelectedError`), not here, so the message can name the fields.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.evals.sampling import EXCLUDED_ENVIRONMENTS

MAX_ENVIRONMENTS = 20
MAX_RANDOM_RATE = Decimal("0.05")

Environment = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[^\x00]+$")]


class ReviewPolicyIn(BaseModel):
    """The body of `PUT /review-policy`; omitted fields take the defaults."""

    model_config = ConfigDict(extra="forbid")

    queue_id: uuid.UUID
    enabled: bool = True
    random_rate: Decimal = Field(
        default=Decimal("0.01"), ge=0, le=MAX_RANDOM_RATE, decimal_places=4
    )
    include_errors: bool = True
    include_cost_outliers: bool = True
    include_doctor_findings: bool = True
    daily_cap: int = Field(default=50, ge=1, le=500)
    environments: list[Environment] = Field(default_factory=list, max_length=MAX_ENVIRONMENTS)

    @field_validator("environments")
    @classmethod
    def _no_internal_environment(cls, value: list[str]) -> list[str]:
        internal = sorted(set(value) & EXCLUDED_ENVIRONMENTS)
        if internal:
            raise ValueError(f"internal environments cannot be reviewed: {', '.join(internal)}")
        return value

    def selects_nothing(self) -> bool:
        return (
            not (self.include_errors or self.include_cost_outliers or self.include_doctor_findings)
            and self.random_rate == 0
        )


class RunRequest(BaseModel):
    """The body of `POST /review-policy/runs`: the UTC day to select."""

    model_config = ConfigDict(extra="forbid")

    day: date

"""The review policy and its selection history.

The bodies that set a policy and ask for a run are owned by the review domain
(`app.review.schemas`), which validates them; they are re-exported here so routers import from
one place. `random_rate` is a 4-place string, as every rate the API answers.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import PlainSerializer

from app.api.schemas.common import ApiModel
from app.db.models import ReviewPolicy, ReviewSelection
from app.review.schemas import ReviewPolicyIn, RunRequest

__all__ = [
    "ReviewPolicyIn",
    "ReviewPolicyOut",
    "RunRequest",
    "RunRequestedOut",
    "SelectionOut",
]

_PLACES = Decimal("0.0001")

Rate = Annotated[
    Decimal, PlainSerializer(lambda v: format(v.quantize(_PLACES), "f"), return_type=str)
]


class SelectionOut(ApiModel):
    day: date
    queue_id: uuid.UUID | None
    candidates: dict[str, int]
    queued: dict[str, int]
    skipped_duplicates: int
    cap_hit: bool
    ran_at: datetime
    duration_ms: int | None

    @classmethod
    def of(cls, selection: ReviewSelection) -> "SelectionOut":
        return cls.model_validate(selection)


class ReviewPolicyOut(ApiModel):
    id: uuid.UUID
    queue_id: uuid.UUID
    queue_name: str
    enabled: bool
    random_rate: Rate
    include_errors: bool
    include_cost_outliers: bool
    include_doctor_findings: bool
    daily_cap: int
    environments: list[str]
    next_run_at: datetime | None
    last_selection: SelectionOut | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(
        cls,
        policy: ReviewPolicy,
        *,
        queue_name: str,
        next_run_at: datetime | None,
        last_selection: ReviewSelection | None,
    ) -> "ReviewPolicyOut":
        return cls(
            id=policy.id,
            queue_id=policy.queue_id,
            queue_name=queue_name,
            enabled=policy.enabled,
            random_rate=policy.random_rate,
            include_errors=policy.include_errors,
            include_cost_outliers=policy.include_cost_outliers,
            include_doctor_findings=policy.include_doctor_findings,
            daily_cap=policy.daily_cap,
            environments=list(policy.environments),
            next_run_at=next_run_at,
            last_selection=SelectionOut.of(last_selection) if last_selection else None,
            created_at=policy.created_at,
            updated_at=policy.updated_at,
        )


class RunRequestedOut(ApiModel):
    day: date
    job_id: int

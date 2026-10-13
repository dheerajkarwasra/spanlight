"""The price catalogue and the models a project has used without a price."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, Field, StringConstraints

from app.api.schemas.common import ApiModel, Money, UserOut


class PriceOut(ApiModel):
    provider: str
    model_pattern: str
    input_per_mtok: Money
    output_per_mtok: Money
    cached_input_per_mtok: Money | None
    effective_from: datetime
    version: str


class UnpricedModelOut(ApiModel):
    provider: str | None
    model: str
    llm_calls: int
    input_tokens: int
    output_tokens: int


def _as_utc(moment: datetime) -> datetime:
    # A time without an offset is UTC, as for every other datetime the API accepts.
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


# Stored lower-case, as the seed table is (a CHECK in 0205 keeps it so). The price lookup is
# case-sensitive, so an override prices the lower-case spelling of a model only.
_Lowered = AfterValidator(str.lower)
OverrideProvider = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100), _Lowered
]
OverridePattern = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200), _Lowered
]
# `numeric(12, 6)`: below a million dollars per million tokens, six decimals.
Rate = Annotated[Decimal, Field(ge=0, lt=1_000_000, max_digits=12, decimal_places=6)]


class PriceOverrideCreate(ApiModel):
    provider: OverrideProvider
    model_pattern: OverridePattern
    input_per_mtok: Rate
    output_per_mtok: Rate
    cached_input_per_mtok: Rate | None = None
    effective_from: Annotated[datetime, AfterValidator(_as_utc)] | None = None


class PriceOverrideOut(ApiModel):
    id: uuid.UUID
    provider: str
    model_pattern: str
    input_per_mtok: Money
    output_per_mtok: Money
    cached_input_per_mtok: Money | None
    effective_from: datetime
    created_by: UserOut | None
    created_at: datetime

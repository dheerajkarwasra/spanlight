"""Building blocks shared by every schema module: the base model, pagination, money and names.

`UserOut` lives here because users are embedded in org, member, audit and API key shapes, and
those modules would otherwise import each other in a cycle.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, PlainSerializer, StringConstraints


def _format_money(value: Decimal) -> str:
    # format(..., "f") avoids scientific notation such as "0E-8".
    return format(value, "f")


Money = Annotated[Decimal, PlainSerializer(_format_money, return_type=str, when_used="json")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Confirmation = Annotated[str, StringConstraints(max_length=200)]
"""What a person typed to confirm a deletion: compared as typed with the slug of what is deleted.

Never stripped or case-folded, and not required to be non-empty: a blank or padded answer is a
mismatch (`CONFIRMATION_MISMATCH`) like any other wrong one. The cap keeps a pasted novel out.
"""


def _in_the_future(moment: datetime) -> datetime:
    # A time without an offset is UTC, as for every other datetime the API accepts.
    moment = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment
    if moment <= datetime.now(UTC):
        raise ValueError("must be in the future")
    try:
        return moment.astimezone(UTC)
    except OverflowError as exc:
        # 9999-12-31 in a zone behind UTC is already year 10000 in UTC, which has no datetime.
        raise ValueError("is too far in the future") from exc


FutureTime = Annotated[datetime, AfterValidator(_in_the_future)]
"""An expiry: a time after now, as UTC. Used as `FutureTime | None`, where `None` means never."""


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[ItemT](BaseModel):
    items: list[ItemT]
    next_cursor: str | None


class UserOut(ApiModel):
    id: uuid.UUID
    email: str
    name: str
    created_at: datetime
    email_verified: bool


class AggregateMetadata(BaseModel):
    """Unavailable values are retained materialized snapshots; absent values remain unknown."""

    aggregate_status: Literal["complete", "pending", "unavailable"] = "complete"
    oldest_pending_at: datetime | None = None


AGGREGATE_RESPONSE_HEADERS: dict[int | str, dict[str, Any]] = {
    200: {
        "headers": {
            "Aggregate-Status": {
                "description": "Source coverage, including when the response array is empty.",
                "schema": {"type": "string", "enum": ["complete", "pending", "unavailable"]},
            },
            "Aggregate-Oldest-Pending-At": {
                "description": (
                    "Oldest pending correction time; omitted when no correction is pending."
                ),
                "schema": {"type": "string", "format": "date-time"},
            },
        }
    }
}

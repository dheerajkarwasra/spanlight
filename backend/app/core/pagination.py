"""Opaque keyset cursors.

A cursor encodes the sort key of the last item on the previous page. It is
base64url JSON: opaque to clients, but not a secret (it carries no
authorization; every query is still scoped to the caller's org/project).
"""

import base64
import binascii
import json
import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import Query

from app.core.errors import FieldError, ProblemError

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

PageLimit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]


def encode_cursor(sort_value: datetime, tiebreaker: str) -> str:
    payload = json.dumps({"s": sort_value.isoformat(), "t": tiebreaker}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        sort_value = datetime.fromisoformat(payload["s"])
        tiebreaker = payload["t"]
        if not isinstance(tiebreaker, str) or sort_value.tzinfo is None:
            raise ValueError("malformed cursor")
    except (binascii.Error, ValueError, KeyError, TypeError, RecursionError) as exc:
        raise _invalid_cursor() from exc
    return sort_value, tiebreaker


def _invalid_cursor() -> ProblemError:
    return ProblemError(
        422,
        "VALIDATION_ERROR",
        "The pagination cursor is invalid.",
        errors=[FieldError(field="cursor", message="Invalid cursor")],
    )


def decode_uuid_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    sort_value, tiebreaker = decode_cursor(cursor)
    try:
        return sort_value, uuid.UUID(tiebreaker)
    except ValueError as exc:
        raise _invalid_cursor() from exc


# A sort value is a count or a cost, so a cursor that is longer, or has a larger exponent or more
# digits, is not one we issued, and casting it to `numeric` in a query could fail.
_MAX_VALUE_EXPONENT = 40
_MAX_VALUE_DIGITS = 40
_MAX_VALUE_CURSOR_LENGTH = 1024
_MAX_TIEBREAKER_LENGTH = 256


def _is_bindable_text(text: str) -> bool:
    return all(char != "\x00" and not 0xD800 <= ord(char) <= 0xDFFF for char in text)


def _is_plausible_value(value: Decimal) -> bool:
    _, digits, exponent = value.as_tuple()
    return (
        isinstance(exponent, int)
        and len(digits) <= _MAX_VALUE_DIGITS
        and abs(exponent) <= _MAX_VALUE_EXPONENT
        and abs(value.adjusted()) <= _MAX_VALUE_EXPONENT
    )


def encode_value_cursor(value: Decimal | int | None, tiebreaker: str) -> str:
    """A cursor for a list sorted by a number, largest first, rows without one last.

    `None` is the sort value of the rows that have none (an unknown cost); they come after every
    number, so a cursor at one continues among the rest of them.
    """
    payload = json.dumps(
        {"v": None if value is None else format(Decimal(value), "f"), "t": tiebreaker},
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_value_cursor(cursor: str) -> tuple[Decimal | None, str]:
    """The sort value (`None`: the rows without one) and tiebreaker `encode_value_cursor` wrote.

    Anything it could not have written is the usual invalid cursor error, so a forged cursor
    never reaches the database.
    """
    try:
        if len(cursor) > _MAX_VALUE_CURSOR_LENGTH:
            raise ValueError("cursor too long")
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        raw, tiebreaker = payload["v"], payload["t"]
        if not isinstance(tiebreaker, str) or not (raw is None or isinstance(raw, str)):
            raise ValueError("malformed cursor")
        # NUL and lone surrogates cannot be bound as text parameters.
        if len(tiebreaker) > _MAX_TIEBREAKER_LENGTH or not _is_bindable_text(tiebreaker):
            raise ValueError("malformed tiebreaker")
        value = None if raw is None else Decimal(raw)
        if value is not None and not _is_plausible_value(value):
            raise ValueError("malformed cursor")
    except (
        binascii.Error,
        ValueError,
        KeyError,
        TypeError,
        RecursionError,
        InvalidOperation,
    ) as exc:
        raise _invalid_cursor() from exc
    return value, tiebreaker


def encode_sort_cursor(value: Decimal | None, started_at: datetime, tiebreaker: str) -> str:
    """A cursor for a list sorted by a number, largest first and rows without one last, then by
    a start time and a tiebreaker, both descending (the trace list sorted by retry cost)."""
    payload = json.dumps(
        {
            "v": None if value is None else format(value, "f"),
            "s": started_at.isoformat(),
            "t": tiebreaker,
        },
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_sort_cursor(cursor: str) -> tuple[Decimal | None, datetime, str]:
    """The sort value, start time and tiebreaker `encode_sort_cursor` wrote.

    A cursor of another list (one without the start time) or anything it could not have written
    is the usual invalid cursor error.
    """
    try:
        if len(cursor) > _MAX_VALUE_CURSOR_LENGTH:
            raise ValueError("cursor too long")
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        raw, started_raw, tiebreaker = payload["v"], payload["s"], payload["t"]
        if not (raw is None or isinstance(raw, str)) or not isinstance(started_raw, str):
            raise ValueError("malformed cursor")
        if not isinstance(tiebreaker, str) or len(tiebreaker) > _MAX_TIEBREAKER_LENGTH:
            raise ValueError("malformed tiebreaker")
        if not _is_bindable_text(tiebreaker):
            raise ValueError("malformed tiebreaker")
        started_at = datetime.fromisoformat(started_raw)
        value = None if raw is None else Decimal(raw)
        if started_at.tzinfo is None or (value is not None and not _is_plausible_value(value)):
            raise ValueError("malformed cursor")
    except (
        binascii.Error,
        ValueError,
        KeyError,
        TypeError,
        RecursionError,
        InvalidOperation,
    ) as exc:
        raise _invalid_cursor() from exc
    return value, started_at, tiebreaker

"""The metadata filter of the trace list and the trace export (spec §6.3).

Key and value together keep the traces whose metadata contains that pair; the key alone keeps
the traces without the key (the "none" drill-down); a value alone is a validation error. Both
are bound as parameters, never interpolated. There is no index to help: under row-level
security Postgres cannot use one for `@>`, so the caller's `started_at` window bounds the scan.
"""

from sqlalchemy import Text, cast, func, not_
from sqlalchemy.sql.elements import ColumnElement

from app.core.errors import ProblemError
from app.db.models import Trace

METADATA_VALUE_NEEDS_KEY = "metadata_value needs metadata_key."


def check_metadata_filter(key: str | None, value: str | None) -> None:
    """Reject a value without the key it belongs to."""
    if key is None and value is not None:
        raise ProblemError(
            422,
            "VALIDATION_ERROR",
            "The request is invalid.",
            errors=[{"field": "metadata_value", "message": METADATA_VALUE_NEEDS_KEY}],
        )


def metadata_conditions(key: str | None, value: str | None) -> list[ColumnElement[bool]]:
    """The trace conditions for the filter; empty when no key was given."""
    if key is None:
        return []
    if value is None:
        return [not_(Trace.metadata_.has_key(cast(key, Text)))]
    # Both casts type the binds: `jsonb_build_object` is variadic `"any"`.
    pair = func.jsonb_build_object(cast(key, Text), cast(value, Text))
    return [Trace.metadata_.op("@>")(pair)]

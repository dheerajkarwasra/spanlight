"""Trace-list filters: trace columns, spans the trace contains, scores and retries.

Text search (`q`) needs the project and window: `app.explorer.content_search`.

Every filter is optional; a trace passes when it matches all the given ones.
"""

import uuid
from decimal import Decimal
from typing import Literal

from sqlalchemy import Select
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import Span, Trace
from app.explorer.metadata_filters import metadata_conditions
from app.explorer.score_filters import score_conditions
from app.explorer.trace_queries import has_span
from app.ingest.error_class import ErrorClass


def apply_filters[*Ts](
    query: Select[*Ts],
    *,
    name: str | None = None,
    environment: str | None = None,
    release: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    tag: str | None = None,
    metadata_key: str | None = None,
    metadata_value: str | None = None,
    status: Literal["ok", "error"] | None = None,
    model: str | None = None,
    error_class: ErrorClass | None = None,
    prompt_version_id: uuid.UUID | None = None,
    score_name: str | None = None,
    score_min: Decimal | None = None,
    score_max: Decimal | None = None,
    score_value: str | None = None,
    has_scores: bool | None = None,
    has_retries: bool | None = None,
) -> Select[*Ts]:
    """`query` (over `Trace`) narrowed to the traces that match every given filter."""
    equal = (
        (Trace.name, name),
        (Trace.environment, environment),
        (Trace.release, release),
        (Trace.external_user_id, user_id),
        (Trace.session_id, session_id),
    )
    conditions = [column == value for column, value in equal if value is not None]
    if tag is not None:
        conditions.append(Trace.tags.contains([tag]))
    conditions += metadata_conditions(metadata_key, metadata_value)
    conditions += _status_conditions(status, has_retries)
    conditions += [
        has_span(condition) for condition in _span_conditions(model, error_class, prompt_version_id)
    ]
    conditions += score_conditions(score_name, score_min, score_max, score_value, has_scores)
    return query.where(*conditions)


def _status_conditions(
    status: Literal["ok", "error"] | None, has_retries: bool | None
) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = []
    if status == "error":
        conditions.append(Trace.error_count > 0)
    elif status == "ok":
        conditions.append(Trace.error_count == 0)
    # Both sides are known values: a trace not recomputed since migration 0700 (NULL) is in
    # neither, since whether it had retries is unknown.
    if has_retries is True:
        conditions.append(Trace.retry_calls > 0)
    elif has_retries is False:
        conditions.append(Trace.retry_calls == 0)
    return conditions


def _span_conditions(
    model: str | None, error_class: ErrorClass | None, prompt_version_id: uuid.UUID | None
) -> list[ColumnElement[bool]]:
    """The filters a trace passes when at least one of its spans matches."""
    conditions: list[ColumnElement[bool]] = []
    if model is not None:
        conditions.append(Span.model == model)
    if error_class is not None:
        conditions.append(Span.error_class == error_class.value)
    if prompt_version_id is not None:
        conditions.append(Span.prompt_version_id == prompt_version_id)
    return conditions

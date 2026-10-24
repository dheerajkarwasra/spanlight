"""The trace list's filter parameters, as one dependency.

The list and the histogram both take it, so a filter added to one reaches the other.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import Depends, Query
from sqlalchemy import Select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.window import TimeWindow
from app.core.db_errors import is_statement_timeout
from app.db.models import Trace
from app.db.timeouts import set_statement_limit
from app.explorer.content_search import (
    SEARCH_TIMEOUT_MS,
    content_start,
    search_too_broad,
    text_search,
)
from app.explorer.metadata_filters import check_metadata_filter
from app.explorer.score_filters import check_score_filters
from app.explorer.trace_filters import apply_filters
from app.ingest.error_class import ErrorClass
from app.ingest.metadata import MetadataKey

OptionalText = Annotated[str | None, Query(max_length=256)]


@dataclass(frozen=True)
class TraceListFilters:
    name: OptionalText = None
    environment: OptionalText = None
    release: OptionalText = None
    model: OptionalText = None
    status: Literal["ok", "error"] | None = None
    error_class: ErrorClass | None = None
    prompt_version_id: uuid.UUID | None = None
    score_name: OptionalText = None
    score_min: Decimal | None = None
    score_max: Decimal | None = None
    score_value: OptionalText = None
    has_scores: bool | None = None
    has_retries: bool | None = None
    user_id: OptionalText = None
    session_id: OptionalText = None
    tag: OptionalText = None
    metadata_key: Annotated[MetadataKey | None, Query()] = None
    metadata_value: OptionalText = None
    q: Annotated[
        str | None,
        Query(
            max_length=256,
            description=(
                "Matches the trace name (substring, ignoring case), an exact trace id, or, with 3+ "
                "characters, a preview of the input and output of a span started in the window."
            ),
        ),
    ] = None

    def __post_init__(self) -> None:
        check_score_filters(self.score_name, self.score_min, self.score_max, self.score_value)
        check_metadata_filter(self.metadata_key, self.metadata_value)

    async def narrow[*Ts](
        self, db: AsyncSession, query: Select[*Ts], project_id: uuid.UUID, window: TimeWindow
    ) -> Select[*Ts]:
        """`query` (over `Trace`) narrowed to the project's traces started in `window` that match
        every filter. A `q` search also bounds the transaction's statements to its time limit:
        run the query inside `too_broad_on_timeout()`."""
        query = apply_filters(
            query.where(
                Trace.project_id == project_id,
                Trace.started_at >= window.start,
                Trace.started_at < window.end,
            ),
            name=self.name,
            environment=self.environment,
            release=self.release,
            user_id=self.user_id,
            session_id=self.session_id,
            tag=self.tag,
            metadata_key=self.metadata_key,
            metadata_value=self.metadata_value,
            status=self.status,
            model=self.model,
            error_class=self.error_class,
            prompt_version_id=self.prompt_version_id,
            score_name=self.score_name,
            score_min=self.score_min,
            score_max=self.score_max,
            score_value=self.score_value,
            has_scores=self.has_scores,
            has_retries=self.has_retries,
        )
        if not self.q:
            return query
        await set_statement_limit(db, SEARCH_TIMEOUT_MS)
        content_from = await content_start(db, window.start, window.end)
        return query.where(text_search(self.q, project_id, content_from, window.end))

    @contextmanager
    def too_broad_on_timeout(self) -> Iterator[None]:
        """A `q` search that runs out of time is `422 SEARCH_TOO_BROAD`, not a 500."""
        try:
            yield
        except OperationalError as error:
            if self.q and is_statement_timeout(error.orig):
                raise search_too_broad() from None
            raise


TraceFilters = Annotated[TraceListFilters, Depends()]

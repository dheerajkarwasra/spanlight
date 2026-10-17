"""The attribution branch of the `create_export` job: the grouped table of a window as CSV.

The rows come from the same read as `GET .../attribution` (`app.attribution.service`), in keyset
pages, each under a project-bound session with the trace export's statement timeout. Values come
first, by cost descending with unknowns last; the `none` bucket and, on the rollup path, the
`other` fold close the file. Every cell goes through `csv_line`, so a value that reads as a
formula opens as text.
"""

import uuid
from collections.abc import AsyncIterator, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.schemas.attribution import AttributionOut, AttributionTotalsOut
from app.api.window import TimeWindow
from app.attribution.service import AttributionRequest, read_attribution
from app.core.errors import ProblemError
from app.db.models import Project
from app.exports.job_support import (
    STATEMENT_TIMEOUT_MS,
    ExportFailedError,
    Tally,
    TooLargeError,
    bound,
)
from app.exports.schemas import AttributionExportIn
from app.exports.service import MAX_EXPORT_TRACES
from app.exports.writers import csv_line, money_cell

ATTRIBUTION_CSV_COLUMNS: tuple[str, ...] = (
    "group_by",
    "key",
    "value",
    "bucket",
    "traces",
    "llm_calls",
    "errors",
    "unpriced_calls",
    "input_tokens",
    "output_tokens",
    "cost_usd",
    "retries",
    "retry_cost_usd",
    "folded_values",
)
PAGE_SIZE = 1000


def attribution_csv_line(
    group_by: str,
    key: str | None,
    bucket: str,
    figures: AttributionTotalsOut,
    *,
    value: str = "",
    folded_values: int | None = None,
) -> bytes:
    """One row of the table. An unknown value is an empty cell, never `0`."""
    return csv_line(
        (
            group_by,
            key or "",
            value,
            bucket,
            str(figures.traces),
            str(figures.llm_calls),
            str(figures.errors),
            str(figures.unpriced_calls),
            str(figures.input_tokens),
            str(figures.output_tokens),
            money_cell(figures.cost_usd),
            "" if figures.retries is None else str(figures.retries),
            money_cell(figures.retry_cost_usd),
            "" if folded_values is None else str(folded_values),
        )
    )


async def _promoted_keys(db: AsyncSession, project_id: uuid.UUID) -> Sequence[str]:
    keys = await db.scalar(select(Project.promoted_metadata_keys).where(Project.id == project_id))
    return keys or ()


async def _pages(
    factory: async_sessionmaker[AsyncSession], project_id: uuid.UUID, request: AttributionExportIn
) -> AsyncIterator[AttributionOut]:
    """Every page of the table, each read in its own short, project-bound transaction.

    Raises `ExportFailedError` with the problem's code when the read refuses the request.
    """
    window = TimeWindow(request.filters.from_, request.filters.to)
    cursor: str | None = None
    while True:
        async with bound(factory, project_id, statement_timeout_ms=STATEMENT_TIMEOUT_MS) as db:
            keys = await _promoted_keys(db, project_id)
            page = AttributionRequest(
                request.group_by,
                request.key,
                window,
                request.filters.environment,
                "cost",
                PAGE_SIZE,
                cursor,
            )
            try:
                out = await read_attribution(db, project_id, keys, page)
            except ProblemError as problem:
                # The request was valid when queued (a key un-promoted since): no retry helps.
                raise ExportFailedError(problem.code) from problem
        yield out
        cursor = out.next_cursor
        if cursor is None:
            return


async def attribution_batches(
    factory: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
    request: AttributionExportIn,
    tally: Tally,
) -> AsyncIterator[bytes]:
    """The file's bytes, one chunk per row, tallying rows and size as it goes."""
    group_by, key = request.group_by, request.key

    def counted(chunk: bytes) -> bytes:
        tally.rows += 1
        if tally.rows > MAX_EXPORT_TRACES:
            raise TooLargeError
        tally.size_bytes += len(chunk)
        return chunk

    header = csv_line(ATTRIBUTION_CSV_COLUMNS)
    tally.size_bytes += len(header)
    yield header
    async for page in _pages(factory, project_id, request):
        for item in page.items:
            yield counted(attribution_csv_line(group_by, key, "value", item, value=item.value))
    # Every page carries the buckets; the last one is as current as the rows.
    if page.none is not None:
        yield counted(attribution_csv_line(group_by, key, "none", page.none))
    if page.other is not None:
        other = attribution_csv_line(
            group_by, key, "other", page.other, folded_values=page.other.folded_values
        )
        yield counted(other)

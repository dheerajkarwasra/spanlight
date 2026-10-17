"""Dataset reads and writes. Every query runs in a transaction bound to the project, so row-level
security filters it; the `project_id` and `dataset_id` conditions are the second line."""

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, exists, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import new_id
from app.datasets.schemas import DatasetItemIn, SpanSource, TraceWithSpans
from app.db.models import Dataset, DatasetItem, Experiment, Span, Trace
from app.db.models.telemetry import SpanKind


async def list_datasets(
    db: AsyncSession,
    project_id: uuid.UUID,
    *,
    name: str | None,
    after: uuid.UUID | None,
    limit: int,
) -> list[tuple[Dataset, int]]:
    """Newest first (ids are time-ordered), up to `limit + 1` rows so the caller sees whether
    another page follows. `name` matches exactly."""
    query = (
        select(Dataset)
        .where(Dataset.project_id == project_id)
        .order_by(Dataset.id.desc())
        .limit(limit + 1)
    )
    if name is not None:
        query = query.where(Dataset.name == name)
    if after is not None:
        query = query.where(Dataset.id < after)
    datasets = list((await db.scalars(query)).all())
    # Count only the datasets of this page, not every item of the project.
    counts = dict(
        (
            await db.execute(
                select(DatasetItem.dataset_id, func.count())
                .where(DatasetItem.dataset_id.in_([dataset.id for dataset in datasets]))
                .group_by(DatasetItem.dataset_id)
            )
        ).all()
    )
    return [(dataset, int(counts.get(dataset.id, 0))) for dataset in datasets]


async def get_dataset(
    db: AsyncSession, project_id: uuid.UUID, dataset_id: uuid.UUID, *, lock: bool = False
) -> Dataset | None:
    query = select(Dataset).where(Dataset.project_id == project_id, Dataset.id == dataset_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return await db.scalar(query)


async def dataset_in_use(db: AsyncSession, dataset_id: uuid.UUID) -> bool:
    """Whether an experiment uses the dataset. The single place that knows its referrers."""
    return bool(await db.scalar(select(exists().where(Experiment.dataset_id == dataset_id))))


async def count_items(db: AsyncSession, dataset_id: uuid.UUID) -> int:
    count = await db.scalar(
        select(func.count()).select_from(DatasetItem).where(DatasetItem.dataset_id == dataset_id)
    )
    return int(count or 0)


async def list_items(
    db: AsyncSession, dataset_id: uuid.UUID, *, after: uuid.UUID | None, limit: int
) -> list[DatasetItem]:
    """By id ascending, up to `limit + 1` rows."""
    query = (
        select(DatasetItem)
        .where(DatasetItem.dataset_id == dataset_id)
        .order_by(DatasetItem.id)
        .limit(limit + 1)
    )
    if after is not None:
        query = query.where(DatasetItem.id > after)
    return list((await db.scalars(query)).all())


async def get_item(
    db: AsyncSession, dataset_id: uuid.UUID, item_id: uuid.UUID
) -> DatasetItem | None:
    return await db.scalar(
        select(DatasetItem).where(DatasetItem.dataset_id == dataset_id, DatasetItem.id == item_id)
    )


async def insert_items(
    db: AsyncSession, dataset_id: uuid.UUID, items: Sequence[DatasetItemIn]
) -> list[uuid.UUID]:
    """Store the items in one statement and return their ids, in order. The caller holds the
    dataset's row lock."""
    if not items:
        return []
    ids = [new_id() for _ in items]
    await db.execute(
        insert(DatasetItem),
        [
            {
                "id": item_id,
                "dataset_id": dataset_id,
                "input": item.input,
                "expected_output": item.expected_output,
                "metadata": item.metadata,
                "source_trace_id": item.source_trace_id,
                "source_span_id": item.source_span_id,
            }
            for item_id, item in zip(ids, items, strict=True)
        ],
    )
    return ids


async def delete_item(db: AsyncSession, dataset_id: uuid.UUID, item_id: uuid.UUID) -> bool:
    result = await db.execute(
        delete(DatasetItem).where(DatasetItem.dataset_id == dataset_id, DatasetItem.id == item_id)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def load_traces(
    db: AsyncSession,
    project_id: uuid.UUID,
    trace_ids: Sequence[str],
    span_id: str | None,
) -> list[TraceWithSpans]:
    """The project's traces among `trace_ids`, each with one candidate span: the named span, else
    its first `llm` span. Traces of other projects are absent, as are unknown ids."""
    environments = {
        row.trace_id: row.environment
        for row in await db.execute(
            select(Trace.trace_id, Trace.environment).where(
                Trace.project_id == project_id, Trace.trace_id.in_(trace_ids)
            )
        )
    }
    query = (
        select(
            Span.trace_id,
            Span.span_id,
            Span.kind,
            Span.started_at,
            Span.model,
            Span.input,
            Span.output,
        )
        .where(Span.project_id == project_id, Span.trace_id.in_(list(environments)))
        .distinct(Span.trace_id)
        .order_by(Span.trace_id, Span.started_at, Span.span_id)
    )
    query = (
        query.where(Span.span_id == span_id) if span_id else query.where(Span.kind == SpanKind.LLM)
    )
    spans = {
        row.trace_id: SpanSource(
            span_id=row.span_id,
            kind=row.kind.value,
            started_at=row.started_at,
            model=row.model,
            input=row.input,
            output=row.output,
        )
        for row in await db.execute(query)
    }
    return [
        TraceWithSpans(
            trace_id=trace_id,
            environment=environment,
            spans=[spans[trace_id]] if trace_id in spans else [],
        )
        for trace_id, environment in environments.items()
    ]

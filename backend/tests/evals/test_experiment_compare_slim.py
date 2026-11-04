"""Equality gate: comparing two runs reads only what the comparison shows, with the same result.

Runs a and b compare through the database loader; the reference builds the same comparison from
the full stored rows, telling copies apart by their JSON as Python reads it back. The fixtures
cover matched, edited and one-sided items, a legacy row without a copy, a JSON-null input,
`true` against `1`, key order, failed items, an evaluator error, a cluster key and a run whose
cost is unknown.
"""

import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.schemas.experiment_compare import ComparisonOut
from app.db.models import Experiment, ExperimentItem
from app.db.rls import bind_project, bypass_rls
from app.evals import experiment_queries as queries
from app.evals.compare import ItemRow, compare_runs
from app.evals.experiment_compare import compare_experiment_runs
from app.evals.experiment_totals import cluster_label
from tests.rls.factories import create, create_project

LIMIT = 1000
KEY = "topic"


def _id(number: int) -> uuid.UUID:
    return uuid.UUID(int=number)


def _item(
    number: int,
    *,
    input: Any = "q",
    expected: Any = "a",
    metadata: dict[str, Any] | None = None,
    copy: bool = True,
    scores: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
    cost: str | None = "0.001",
    latency: float | None = 120.0,
    error: str | None = None,
    evaluator_error: str | None = None,
) -> dict[str, Any]:
    return {
        "dataset_item_id": _id(number),
        "trace_id": f"{number:032x}",
        "output": {"text": f"answer {number}", "finish_reason": "stop"}
        if output is None and error is None
        else output,
        "input": (JSON.NULL if input is None else input) if copy else None,
        "expected_output": expected if copy else None,
        "item_metadata": ({KEY: f"t{number % 3}"} if metadata is None else metadata)
        if copy
        else None,
        "scores": {"quality": 0.5, "pass": True, "tone": "calm"} if scores is None else scores,
        "latency_ms": latency,
        "cost_usd": None if cost is None else Decimal(cost),
        "error": error,
        "evaluator_error": evaluator_error,
    }


RUN_A = [
    _item(1),
    _item(2, expected="Paris"),
    _item(3, input={"question": "capital?"}),
    _item(4, input={"a": 1, "b": 2}),
    _item(5, expected=True),
    _item(6, input=None, expected=None),
    _item(7),
    _item(8, scores={"quality": 0.2}),
    _item(10, copy=False),
    _item(11),
    _item(12, metadata={}),
]
RUN_B = [
    _item(1, scores={"quality": 0.9, "pass": False, "tone": "calm"}),
    _item(2, expected="Lyon", scores={"quality": 0.9, "pass": True, "tone": "sharp"}),
    _item(3, input={"question": "capital of France?"}),
    _item(4, input={"b": 2, "a": 1}, scores={"quality": 0.7, "pass": True, "tone": "calm"}),
    _item(5, expected=1),
    _item(6, input=None, expected=None, scores={"quality": 0.1, "pass": False, "tone": "x"}),
    _item(7, error="provider timed out", cost=None, latency=None, scores={}),
    _item(9, input={"only": "b"}),
    _item(10, input="b's copy", scores={"quality": 0.6, "pass": False, "tone": "calm"}),
    _item(11, evaluator_error="judge timed out", scores={}),
    _item(12, metadata={KEY: "t0"}),
]
# Every cost and latency unknown: the cost and p95 deltas against it are null, never 0.
RUN_C = [_item(1, cost=None, latency=None), _item(2, cost=None, latency=None)]


async def _seed(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    """A project, an experiment clustered by `KEY` and three ended runs (a, b, c)."""
    async with sessions() as db, db.begin():
        await bypass_rls(db)
        project_id = await create_project(db)
        experiment = await create(db, "experiments", project_id)
        await db.execute(
            update(Experiment)
            .where(Experiment.id == experiment["id"])
            .values(config={"cluster_key": KEY})
        )
        runs = []
        for number, (status, items) in enumerate(
            [("done", RUN_A), ("failed", RUN_B), ("done", RUN_C)], start=1
        ):
            run_id = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO experiment_runs "
                    "(id, experiment_id, number, target, status, finished_at) "
                    "VALUES (:id, :experiment_id, :number, '{}', :status, now())"
                ),
                {
                    "id": run_id,
                    "experiment_id": experiment["id"],
                    "number": number,
                    "status": status,
                },
            )
            await db.execute(insert(ExperimentItem), [{"run_id": run_id, **i} for i in items])
            runs.append(run_id)
    return project_id, experiment["id"], runs


async def _reference(db: AsyncSession, run_id: uuid.UUID) -> list[ItemRow]:
    """The run's items from their full stored rows, as the comparison read them before E8."""
    items = await db.scalars(
        select(ExperimentItem)
        .where(ExperimentItem.run_id == run_id)
        .order_by(ExperimentItem.dataset_item_id)
        .limit(LIMIT)
    )
    return [
        ItemRow(
            dataset_item_id=i.dataset_item_id,
            trace_id=i.trace_id,
            output=i.output,
            scores=i.scores or {},
            latency_ms=i.latency_ms,
            cost_usd=i.cost_usd,
            error=i.error,
            evaluator_error=i.evaluator_error,
            input=i.input,
            expected_output=i.expected_output,
            has_copy=i.input is not None or i.item_metadata is not None,
            cluster=cluster_label((i.item_metadata or {}).get(KEY), i.dataset_item_id),
            copy_digest=json.dumps([i.input, i.expected_output], sort_keys=True).encode(),
        )
        for i in items
    ]


async def _both(
    sessions: async_sessionmaker[AsyncSession],
    project_id: uuid.UUID,
    experiment_id: uuid.UUID,
    a: uuid.UUID,
    b: uuid.UUID,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """The API body of the loader's comparison, of the reference, and the regression-check read."""
    async with sessions() as db, db.begin():
        await bind_project(db, project_id)
        loaded = await compare_experiment_runs(db, project_id, experiment_id, a, b, limit=LIMIT)
        slim = await compare_experiment_runs(
            db, project_id, experiment_id, a, b, limit=LIMIT, details=False
        )
        reference = compare_runs(
            await _reference(db, a),
            await _reference(db, b),
            boolean_scores=frozenset(),
        )
    assert all(row.output is None for item in slim.items for row in (item.a, item.b) if row)
    return (
        ComparisonOut.of(loaded).model_dump(mode="json"),
        ComparisonOut.of(reference).model_dump(mode="json"),
        ComparisonOut.of(slim).model_dump(mode="json"),
    )


async def test_compare_equals_the_full_row_comparison(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    project_id, experiment_id, (a, b, c) = await _seed(session_factory)

    for first, second in [(a, b), (b, a), (a, a), (a, c)]:
        loaded, reference, slim = await _both(
            session_factory, project_id, experiment_id, first, second
        )
        assert loaded == reference
        # The regression check reads the aggregate and the run deltas only.
        for field in ("aggregate", "cost_delta_usd", "latency_p95_delta_ms", "changed_items"):
            assert slim[field] == reference[field]

    loaded, _, _ = await _both(session_factory, project_id, experiment_id, a, b)
    changed = {item["dataset_item_id"]: item["changed"] for item in loaded["items"]}
    assert changed == {
        str(_id(1)): False,
        str(_id(2)): True,
        str(_id(3)): True,
        str(_id(4)): False,  # key order is not a change
        str(_id(5)): True,  # `true` is not `1`
        str(_id(6)): False,
        str(_id(7)): False,
        str(_id(11)): False,
        str(_id(12)): False,  # metadata is not compared
        str(_id(10)): None,  # run a's row is legacy
        str(_id(8)): None,
        str(_id(9)): None,
    }
    shown = {item["dataset_item_id"]: item["input"] for item in loaded["items"]}
    assert shown[str(_id(10))] == "b's copy"
    assert shown[str(_id(9))] == {"only": "b"}
    assert shown[str(_id(3))] == {"question": "capital?"}
    assert loaded["changed_items"] == 3
    assert loaded["cost_delta_usd"] is not None
    _, unknown, _ = await _both(session_factory, project_id, experiment_id, a, c)
    assert unknown["cost_delta_usd"] is None
    assert unknown["latency_p95_delta_ms"] is None


async def test_another_project_reads_no_item(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, _, (a, _, _) = await _seed(session_factory)

    other = await _seed(session_factory)
    async with session_factory() as db, db.begin():
        await bind_project(db, other[0])
        assert (
            await queries.compare_items(
                db, a, limit=LIMIT, cluster_key=KEY, outputs=True, copies=True
            )
            == []
        )

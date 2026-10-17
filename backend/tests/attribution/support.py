"""A project of traces across three UTC days, for the attribution tests (Tasks 13 to 16).

`seed_attribution_fixture(session_factory, project_id)` inserts the traces of `TRACES` and their
spans directly, past row-level security, then recomputes each trace's totals with the ingest
path's own statement (`app.ingest.store._recompute_rollups`), so `traces.cost_usd` and the token
totals are exactly what ingestion would store. The Bet 2 retry columns are then set to each
trace's `retries` and `retry_cost_usd`, including `None` for a trace not recomputed since
migration 0700 (nothing in these spans repeats a request hash, so the recompute alone would
leave them at 0).

The window is `[DAYS[0] 00:00, DAYS[2] + 1 day 00:00)` UTC. Two traces of the project lie just
outside it (one the day before, one starting exactly at its end) and another project has a trace
inside it; none of the three may count.

The traces, in the window:

| label | started (UTC) | env | name | user | release | metadata | spans |
|---|---|---|---|---|---|---|---|
| t1 | D0 10:00 | prod | answer | u1 | r1 | customer=acme, a.b-c=x | llm 0.10, llm error 0.05 |
| t2 | D0 23:50 | prod | answer | u2 | - | customer=acme | llm 0.20, llm 0.30 on D1 |
| t3 | D1 12:00 | - | - | - | - | - | tool 0.01 |
| t4 | D1 13:00 | staging | summarize | u1 | r2 | customer=globex, a.b-c=y | (below) |
| t5 | D2 09:00 | prod | summarize | u1 | r2 | customer=globex | llm 0.70 |
| t6 | D2 11:00 | prod | answer | u3 | r1 | a.b-c=z | llm error unpriced |

t4's spans: an unpriced llm call, an llm call at 0.40 and a retrieval span at 0.03.
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bypass_rls
from app.ingest.store import _recompute_rollups
from tests.rls.factories import create_project

SessionFactory = async_sessionmaker[AsyncSession]

DAYS = (date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3))


def at(day_index: int, hour: int, minute: int = 0) -> datetime:
    """`hour:minute` UTC on `DAYS[0] + day_index` days (negative or past the window allowed)."""
    return datetime.combine(DAYS[0], datetime.min.time(), UTC) + timedelta(
        days=day_index, hours=hour, minutes=minute
    )


WINDOW_START = at(0, 0)
WINDOW_END = at(3, 0)


@dataclass(frozen=True)
class SpanSeed:
    kind: str
    started_at: datetime
    cost_usd: Decimal | None
    input_tokens: int | None
    output_tokens: int | None
    status: str = "ok"


@dataclass(frozen=True)
class TraceSeed:
    started_at: datetime
    spans: tuple[SpanSeed, ...]
    environment: str | None = "prod"
    name: str | None = None
    external_user_id: str | None = None
    release: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)
    retries: int | None = 0
    retry_cost_usd: Decimal | None = Decimal(0)


def _llm(
    started_at: datetime,
    cost: str | None,
    tokens: tuple[int | None, int | None],
    status: str = "ok",
) -> SpanSeed:
    price = None if cost is None else Decimal(cost)
    return SpanSeed("llm", started_at, price, tokens[0], tokens[1], status)


TRACES: dict[str, TraceSeed] = {
    "t1": TraceSeed(
        at(0, 10),
        (_llm(at(0, 10), "0.10", (100, 20)), _llm(at(0, 10, 1), "0.05", (10, 0), "error")),
        name="answer",
        external_user_id="u1",
        release="r1",
        metadata={"customer": "acme", "a.b-c": "x"},
        retries=1,
        retry_cost_usd=Decimal("0.05"),
    ),
    "t2": TraceSeed(
        at(0, 23, 50),
        (_llm(at(0, 23, 50), "0.20", (200, 20)), _llm(at(1, 0, 30), "0.30", (300, 30))),
        name="answer",
        external_user_id="u2",
        metadata={"customer": "acme"},
    ),
    "t3": TraceSeed(
        at(1, 12),
        (SpanSeed("tool", at(1, 12), Decimal("0.01"), 7, 3),),
        environment=None,
        retries=None,
        retry_cost_usd=None,
    ),
    "t4": TraceSeed(
        at(1, 13),
        (
            _llm(at(1, 13), None, (50, 5)),
            _llm(at(1, 13, 1), "0.40", (400, 40)),
            SpanSeed("retrieval", at(1, 13, 2), Decimal("0.03"), 30, 0),
        ),
        environment="staging",
        name="summarize",
        external_user_id="u1",
        release="r2",
        metadata={"customer": "globex", "a.b-c": "y"},
        retries=None,
        retry_cost_usd=None,
    ),
    "t5": TraceSeed(
        at(2, 9),
        (_llm(at(2, 9), "0.70", (1000, 100)),),
        name="summarize",
        external_user_id="u1",
        release="r2",
        metadata={"customer": "globex"},
        retries=2,
        retry_cost_usd=None,
    ),
    "t6": TraceSeed(
        at(2, 11),
        (_llm(at(2, 11), None, (None, None), "error"),),
        name="answer",
        external_user_id="u3",
        release="r1",
        metadata={"a.b-c": "z"},
    ),
}

# Traces of the project just outside the window: they must never count.
OUTSIDE: dict[str, TraceSeed] = {
    "before": TraceSeed(at(-1, 12), (_llm(at(-1, 12), "9.00", (9, 9)),), name="answer"),
    "at_end": TraceSeed(at(3, 0), (_llm(at(3, 0), "9.00", (9, 9)),), name="answer"),
}

# A trace of another project inside the window: the explicit project filter must drop it.
OTHER_PROJECT = TraceSeed(at(1, 10), (_llm(at(1, 10), "5.00", (5, 5)),), name="answer")


@dataclass(frozen=True)
class Seeded:
    project_id: uuid.UUID
    other_project_id: uuid.UUID
    trace_ids: dict[str, str]
    start: datetime = WINDOW_START
    end: datetime = WINDOW_END


_INSERT_TRACE = text(
    """
    INSERT INTO traces (project_id, trace_id, name, environment, release, external_user_id,
                        metadata, started_at, ended_at)
    VALUES (:project_id, :trace_id, :name, :environment, :release, :external_user_id,
            CAST(:metadata AS jsonb), :started_at, :started_at)
    """
)
_INSERT_SPAN = text(
    """
    INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, started_at, ended_at,
                       provider, model, input_tokens, output_tokens, cost_usd)
    VALUES (:project_id, :trace_id, :span_id, CAST(:kind AS span_kind), 'call',
            CAST(:status AS span_status), :started_at, :started_at + interval '1 second',
            'openai', 'model-a', :input_tokens, :output_tokens, :cost_usd)
    """
)
_SET_RETRIES = text(
    """
    UPDATE traces SET retry_calls = :retries, retry_cost_usd = :retry_cost_usd
    WHERE project_id = :project_id AND trace_id = :trace_id
    """
)


def _trace_params(project_id: uuid.UUID, trace_id: str, seed: TraceSeed) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "trace_id": trace_id,
        "name": seed.name,
        "environment": seed.environment,
        "release": seed.release,
        "external_user_id": seed.external_user_id,
        "metadata": json.dumps(seed.metadata),
        "started_at": seed.started_at,
    }


def _span_params(project_id: uuid.UUID, trace_id: str, seed: SpanSeed) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "trace_id": trace_id,
        "span_id": uuid.uuid4().hex[:16],
        "kind": seed.kind,
        "status": seed.status,
        "started_at": seed.started_at,
        "input_tokens": seed.input_tokens,
        "output_tokens": seed.output_tokens,
        "cost_usd": seed.cost_usd,
    }


async def seed_traces(
    db: AsyncSession, project_id: uuid.UUID, seeds: dict[str, TraceSeed]
) -> dict[str, str]:
    """Insert the traces and spans, recompute their totals, set their retry columns.

    Runs in the caller's session, which must have the worker bypass set. Returns label → trace id.
    """
    trace_ids = {label: uuid.uuid4().hex for label in seeds}
    for label, seed in seeds.items():
        await db.execute(_INSERT_TRACE, _trace_params(project_id, trace_ids[label], seed))
        for span in seed.spans:
            await db.execute(_INSERT_SPAN, _span_params(project_id, trace_ids[label], span))
    await _recompute_rollups(db, project_id, list(trace_ids.values()))
    for label, seed in seeds.items():
        params = {"retries": seed.retries, "retry_cost_usd": seed.retry_cost_usd}
        await db.execute(
            _SET_RETRIES, {**params, "project_id": project_id, "trace_id": trace_ids[label]}
        )
    return trace_ids


async def seed_attribution_fixture(
    session_factory: SessionFactory, project_id: uuid.UUID
) -> Seeded:
    """Seed `TRACES` and `OUTSIDE` into `project_id`, and `OTHER_PROJECT` into a new project."""
    async with session_factory() as db:
        await bypass_rls(db)
        trace_ids = await seed_traces(db, project_id, {**TRACES, **OUTSIDE})
        other_project_id = await create_project(db)
        await seed_traces(db, other_project_id, {"other": OTHER_PROJECT})
        await db.commit()
    return Seeded(project_id, other_project_id, trace_ids)

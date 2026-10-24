"""Evidence spans: the span each evidence trace of an insight opens on.

One span per trace, chosen like the explain excerpts: the first failed span (llm preferred), else
the first llm span, else the first span. Spans are written straight to the tables.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.db.rls import bind_project, bypass_rls
from app.insights import explain_queries
from app.insights.explain_queries import EvidenceSpan
from tests.conftest import BrowserFactory
from tests.helpers import Browser, Workspace, create_workspace
from tests.insights.feedback_support import Sessions, seed_insight

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

_INSERT_TRACE = text(
    "INSERT INTO traces (project_id, trace_id, started_at, ended_at) "
    "VALUES (:project_id, :trace_id, :started_at, :started_at)"
)
_INSERT_SPAN = text(
    "INSERT INTO spans (project_id, trace_id, span_id, kind, name, status, started_at, ended_at, "
    "model) VALUES (:project_id, :trace_id, :span_id, :kind, :span_id, :status, :started_at, "
    ":started_at, :model)"
)


@dataclass(frozen=True)
class S:
    """One span to seed; its name is its span id, and `at` is seconds after `T0`."""

    span_id: str
    kind: str = "llm"
    status: str = "ok"
    at: int = 0
    model: str | None = "gpt-4.1"


@dataclass
class World:
    workspace: Workspace
    outsider: Workspace
    sessions: Sessions

    @property
    def owner(self) -> Browser:
        return self.workspace.owner

    @property
    def project_id(self) -> uuid.UUID:
        return uuid.UUID(self.workspace.project_id)


@pytest.fixture
async def world(browser_factory: BrowserFactory, session_factory: Sessions) -> World:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    outsider = await create_workspace(
        await browser_factory("outsider@example.com"), org_name="Other"
    )
    return World(workspace, outsider, session_factory)


async def seed_trace(sessions: Sessions, project_id: uuid.UUID, spans: list[S]) -> str:
    trace_id = uuid.uuid4().hex
    await seed_trace_with_id(sessions, project_id, trace_id, spans)
    return trace_id


async def seed_trace_with_id(
    sessions: Sessions, project_id: uuid.UUID, trace_id: str, spans: list[S]
) -> None:
    common = {"project_id": project_id, "trace_id": trace_id}
    async with sessions() as db, db.begin():
        await bypass_rls(db)
        await db.execute(_INSERT_TRACE, {**common, "started_at": T0})
        await db.execute(
            _INSERT_SPAN,
            [
                {
                    **common,
                    "span_id": span.span_id,
                    "kind": span.kind,
                    "status": span.status,
                    "started_at": T0 + timedelta(seconds=span.at),
                    "model": span.model,
                }
                for span in spans
            ],
        )


async def evidence_spans(
    sessions: Sessions, project_id: uuid.UUID, trace_ids: list[str]
) -> list[EvidenceSpan]:
    async with sessions() as db, db.begin():
        await bind_project(db, project_id)
        return await explain_queries.evidence_spans(db, project_id, trace_ids)


async def test_evidence_span_prefers_failed_llm_span(world: World) -> None:
    trace_id = await seed_trace(
        world.sessions,
        world.project_id,
        [
            S("a000000000000001", kind="chain", at=0),
            S("a000000000000002", kind="tool", status="error", at=1, model=None),
            S("a000000000000003", status="error", at=2, model="gpt-4.1-mini"),
            S("a000000000000004", status="error", at=3),
        ],
    )
    [span] = await evidence_spans(world.sessions, world.project_id, [trace_id])
    assert span == EvidenceSpan(
        trace_id=trace_id,
        span_id="a000000000000003",
        name="a000000000000003",
        status="error",
        model="gpt-4.1-mini",
    )


async def test_evidence_span_falls_back_to_first_llm_then_first_span(world: World) -> None:
    llm = await seed_trace(
        world.sessions,
        world.project_id,
        [S("b000000000000001", kind="chain", at=0), S("b000000000000002", at=1)],
    )
    plain = await seed_trace(
        world.sessions,
        world.project_id,
        [S("c000000000000002", kind="tool", at=1), S("c000000000000001", kind="chain", at=0)],
    )
    spans = await evidence_spans(world.sessions, world.project_id, [llm, plain])
    assert [(s.span_id, s.status, s.model) for s in spans] == [
        ("b000000000000002", "ok", "gpt-4.1"),
        ("c000000000000001", "ok", "gpt-4.1"),
    ]


async def test_evidence_spans_keep_evidence_order(world: World) -> None:
    ids = [
        await seed_trace(world.sessions, world.project_id, [S(f"d00000000000000{n}")])
        for n in range(3)
    ]
    order = [ids[2], ids[0], ids[1]]
    spans = await evidence_spans(world.sessions, world.project_id, order)
    assert [span.trace_id for span in spans] == order


async def test_evidence_spans_skip_missing_traces(world: World) -> None:
    kept = await seed_trace(world.sessions, world.project_id, [S("e000000000000001")])
    # The same trace id stored in another project is not this project's evidence.
    elsewhere = uuid.uuid4().hex
    outsider_project = uuid.UUID(world.outsider.project_id)
    await seed_trace_with_id(world.sessions, outsider_project, elsewhere, [S("e000000000000002")])
    spans = await evidence_spans(
        world.sessions, world.project_id, [uuid.uuid4().hex, kept, elsewhere]
    )
    assert [span.trace_id for span in spans] == [kept]
    assert await evidence_spans(world.sessions, world.project_id, []) == []


async def test_trace_excerpts_order_unchanged(world: World) -> None:
    trace_id = await seed_trace(
        world.sessions,
        world.project_id,
        [
            S("f000000000000001", kind="chain", at=0, model="chain-model"),
            S("f000000000000002", kind="tool", status="error", at=1, model="tool-model"),
            S("f000000000000003", status="error", at=2, model="failed-llm"),
        ],
    )
    async with world.sessions() as db, db.begin():
        await bind_project(db, world.project_id)
        [excerpt] = await explain_queries.trace_excerpts(db, world.project_id, [trace_id])
    assert (excerpt.trace_id, excerpt.model, excerpt.status) == (trace_id, "failed-llm", "error")


async def test_insight_detail_returns_evidence_spans(world: World) -> None:
    stored = await seed_trace(
        world.sessions, world.project_id, [S("a100000000000001", status="error")]
    )
    gone = uuid.uuid4().hex
    insight_id = await seed_insight(world.sessions, world.project_id)
    async with world.sessions() as db, db.begin():
        await bind_project(db, world.project_id)
        await db.execute(
            text(
                "UPDATE insights SET evidence = jsonb_set(evidence, '{trace_ids}', "
                "CAST(:ids AS jsonb)) WHERE id = :id"
            ),
            {"ids": json.dumps([gone, stored]), "id": insight_id},
        )
    response = await world.owner.get(f"/api/v1/projects/{world.project_id}/insights/{insight_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["evidence"]["trace_ids"] == [gone, stored]
    assert body["evidence_spans"] == [
        {
            "trace_id": stored,
            "span_id": "a100000000000001",
            "name": "a100000000000001",
            "status": "error",
            "model": "gpt-4.1",
        }
    ]

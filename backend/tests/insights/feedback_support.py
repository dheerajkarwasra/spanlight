"""Seeding for the Doctor feedback tests: insights and votes written straight to the database."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.rls import bind_project

Sessions = async_sessionmaker[AsyncSession]

_INSERT_INSIGHT = text(
    "INSERT INTO insights (id, project_id, kind, severity, status, fingerprint, title, summary, "
    "failure_layer, certainty, evidence, suggested_fix, verification, first_seen_at, "
    "last_seen_at, occurrences, resolved_at) "
    "VALUES (:id, :project_id, :kind, 'warning', CAST(:status AS insight_status), :fingerprint, "
    "'title', 'summary', 'provider', 'measured', "
    "jsonb_build_object('trace_ids', '[]'::jsonb, 'metrics', '{}'::jsonb, "
    "'window', jsonb_build_object('start', CAST(:now AS timestamptz), 'end', "
    "CAST(:now AS timestamptz))), 'fix', 'verify', :now, :now, 1, "
    "CASE WHEN :status = 'resolved' THEN CAST(:now AS timestamptz) END)"
)
# `users` has no row-level security: each voter is a user of its own, made inline.
_INSERT_VOTE = text(
    "WITH voter AS (INSERT INTO users (id, email, name) "
    "VALUES (gen_random_uuid(), gen_random_uuid()::text || '@example.com', 'Voter') "
    "RETURNING id) "
    "INSERT INTO insight_feedback (id, project_id, insight_id, user_id, verdict, updated_at) "
    "SELECT gen_random_uuid(), :project_id, :insight_id, voter.id, :verdict, :updated_at "
    "FROM voter"
)


async def seed_insight(
    sessions: Sessions, project_id: uuid.UUID | str, kind: str = "error_spike", status: str = "open"
) -> uuid.UUID:
    insight_id = uuid.uuid4()
    async with sessions() as db, db.begin():
        await bind_project(db, uuid.UUID(str(project_id)))
        await db.execute(
            _INSERT_INSIGHT,
            {
                "id": insight_id,
                "project_id": project_id,
                "kind": kind,
                "status": status,
                "fingerprint": insight_id.hex,
                "now": datetime.now(UTC),
            },
        )
    return insight_id


async def seed_votes(
    sessions: Sessions,
    project_id: uuid.UUID | str,
    insight_id: uuid.UUID,
    *,
    right: int = 0,
    wrong: int = 0,
    updated_at: datetime | None = None,
) -> None:
    """`right` and `wrong` votes on one insight, each from a new user."""
    verdicts = ["right"] * right + ["wrong"] * wrong
    async with sessions() as db, db.begin():
        await bind_project(db, uuid.UUID(str(project_id)))
        for verdict in verdicts:
            await db.execute(
                _INSERT_VOTE,
                {
                    "project_id": project_id,
                    "insight_id": insight_id,
                    "verdict": verdict,
                    "updated_at": updated_at or datetime.now(UTC),
                },
            )


async def votes_of(
    sessions: Sessions, project_id: uuid.UUID | str, insight_id: uuid.UUID
) -> list[tuple[str, str | None]]:
    async with sessions() as db, db.begin():
        await bind_project(db, uuid.UUID(str(project_id)))
        rows = await db.execute(
            text("SELECT verdict, note FROM insight_feedback WHERE insight_id = :id"),
            {"id": insight_id},
        )
        return [(row.verdict, row.note) for row in rows]

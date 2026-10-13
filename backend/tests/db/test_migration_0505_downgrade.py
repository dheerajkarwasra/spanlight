"""Rolling back below 0505 with evaluator scores in the database leaves it upgradable.

Failure mode (full test pass 2026-10-13, T3-01): 0505's downgrade dropped `evaluator_runs` but
left `scores.evaluator_run_id` pointing at the dropped rows, so the next `upgrade head` failed
at 0505's `VALIDATE CONSTRAINT` with a foreign-key violation until someone nulled the column by
hand. The downgrade now does what `ON DELETE SET NULL` would have done.
"""

import uuid

import psycopg
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.migrations import downgrade_to, head_revision, upgrade_to_head
from app.pricing.cost import sync_seed_prices
from tests.conftest import admin_database_url

USER_ID = uuid.UUID("00000000-0000-4000-8000-0000000005a1")
ORG_ID = uuid.UUID("10000000-0000-4000-8000-0000000005a1")
PROJECT_ID = uuid.UUID("20000000-0000-4000-8000-0000000005a1")
EVALUATOR_ID = uuid.UUID("60000000-0000-4000-8000-0000000005a1")
RUN_ID = uuid.UUID("61000000-0000-4000-8000-0000000005a1")
SCORE_ID = uuid.UUID("62000000-0000-4000-8000-0000000005a1")
TRACE_ID = "0505" * 8


def _seed_an_evaluator_score() -> None:
    """A score written by an evaluator run, inserted as the superuser (no RLS context needed)."""
    libpq_url = admin_database_url().replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(libpq_url, autocommit=True) as connection:
        connection.execute(
            "INSERT INTO users (id, email, name, password_hash) VALUES (%s, %s, 'Ada', 'x')",
            (USER_ID, "ada-0505@example.test"),
        )
        connection.execute(
            "INSERT INTO organizations (id, name, slug) VALUES (%s, 'Org', 'org-0505')", (ORG_ID,)
        )
        connection.execute(
            "INSERT INTO projects (id, org_id, name, slug) VALUES (%s, %s, 'Proj', 'proj-0505')",
            (PROJECT_ID, ORG_ID),
        )
        connection.execute(
            "INSERT INTO evaluators (id, project_id, name, kind, config, sampling_rate) "
            "VALUES (%s, %s, 'len', 'heuristic', %s, 1)",
            (EVALUATOR_ID, PROJECT_ID, '{"kind": "heuristic", "check": "max_length"}'),
        )
        connection.execute(
            "INSERT INTO evaluator_runs "
            "(id, evaluator_id, trace_id, status, cost_usd, finished_at) "
            "VALUES (%s, %s, %s, 'done', 0, now())",
            (RUN_ID, EVALUATOR_ID, TRACE_ID),
        )
        connection.execute(
            "INSERT INTO scores (id, project_id, trace_id, name, data_type, value, source, "
            "evaluator_run_id) VALUES (%s, %s, %s, 'len', 'numeric', 0.8, 'eval', %s)",
            (SCORE_ID, PROJECT_ID, TRACE_ID, RUN_ID),
        )


def _score_run_link() -> list[tuple[uuid.UUID, uuid.UUID | None]]:
    """Read as the superuser: row-level security would hide the score from the app role."""
    engine = create_engine(admin_database_url())
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT id, evaluator_run_id FROM scores WHERE id = :id"), {"id": SCORE_ID}
            )
            return [(row[0], row[1]) for row in rows]
    finally:
        engine.dispose()


async def test_downgrade_below_0505_with_an_evaluator_score_can_upgrade_again(
    app_database_url: str, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _seed_an_evaluator_score()

    downgrade_to(app_database_url, "0504")
    try:
        # The score outlives its run, as `ON DELETE SET NULL` promises, and keeps no dangling id.
        assert _score_run_link() == [(SCORE_ID, None)]
    finally:
        # The rest of the session shares this database, so it must end at head; this upgrade is
        # also the step that failed before the fix.
        upgrade_to_head(app_database_url)
        async with session_factory() as db:
            await sync_seed_prices(db)
            await db.commit()

    engine = create_engine(app_database_url)
    try:
        with engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    finally:
        engine.dispose()
    assert version == head_revision()
    assert _score_run_link() == [(SCORE_ID, None)]

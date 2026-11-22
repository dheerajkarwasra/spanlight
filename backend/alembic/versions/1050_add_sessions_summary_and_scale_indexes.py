"""Add incremental sessions_summary rollup table and scale partial indexes.

Revision ID: 1050
Revises: 1049
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "1050"
down_revision: str | None = "1049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROJECT_EXPRESSION = (
    "project_id = (SELECT NULLIF(current_setting('app.project_id', true), '')::uuid)"
)
BYPASS_EXPRESSION = "(SELECT current_setting('app.bypass_rls', true)) = 'on'"
SPANS_AGENT_TRGM_INDEX = "spans_agent_content_trgm_idx"
TRACES_SESSION_ENV_INDEX = "traces_project_env_started_session_idx"


def _index_is_valid(name: str) -> bool | None:
    context = op.get_context()
    if context.as_sql:
        return None
    row = context.bind.execute(  # type: ignore[union-attr]
        sa.text(
            "SELECT i.indisvalid FROM pg_index AS i JOIN pg_class AS c ON c.oid = i.indexrelid "
            "WHERE c.relname = :name AND c.relnamespace = current_schema()::regnamespace"
        ),
        {"name": name},
    ).first()
    return None if row is None else bool(row[0])


def _create_concurrent_index(name: str, ddl: str) -> None:
    state = _index_is_valid(name)
    if state is True:
        return
    if state is False:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
    op.execute(ddl)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE sessions_summary (
            project_id       uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
            environment      text NOT NULL DEFAULT '',
            session_id       text NOT NULL,
            first_at         timestamptz NOT NULL,
            last_at          timestamptz NOT NULL,
            trace_count      bigint NOT NULL DEFAULT 0,
            error_count      bigint NOT NULL DEFAULT 0,
            cost_usd         numeric(14, 8) NOT NULL DEFAULT 0,
            input_tokens     bigint NOT NULL DEFAULT 0,
            output_tokens    bigint NOT NULL DEFAULT 0,
            has_unpriced     boolean NOT NULL DEFAULT false,
            external_user_id text,
            multi_user       boolean NOT NULL DEFAULT false,
            last_trace_id    text,
            PRIMARY KEY (project_id, environment, session_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX sessions_summary_last_env_idx "
        "ON sessions_summary (project_id, environment, last_at DESC, session_id DESC)"
    )
    op.execute(
        "CREATE INDEX sessions_summary_last_idx "
        "ON sessions_summary (project_id, last_at DESC, session_id DESC)"
    )
    op.execute("ALTER TABLE sessions_summary ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sessions_summary FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY sessions_summary_project_isolation ON sessions_summary "
        f"USING ({PROJECT_EXPRESSION}) WITH CHECK ({PROJECT_EXPRESSION})"
    )
    op.execute(
        "CREATE POLICY sessions_summary_worker_bypass ON sessions_summary "
        f"USING ({BYPASS_EXPRESSION}) WITH CHECK ({BYPASS_EXPRESSION})"
    )
    op.execute(
        """
        INSERT INTO sessions_summary (
            project_id, environment, session_id, first_at, last_at,
            trace_count, error_count, cost_usd, input_tokens, output_tokens,
            has_unpriced, external_user_id, multi_user, last_trace_id
        )
        SELECT
            project_id,
            coalesce(environment, ''),
            session_id,
            min(started_at),
            max(ended_at),
            count(*),
            coalesce(sum(error_count), 0),
            coalesce(sum(cost_usd), 0),
            coalesce(sum(input_tokens), 0),
            coalesce(sum(output_tokens), 0),
            coalesce(bool_or(has_unpriced), false),
            CASE
                WHEN count(DISTINCT external_user_id) > 1 THEN NULL
                ELSE max(external_user_id)
            END,
            count(DISTINCT external_user_id) > 1,
            (array_agg(trace_id ORDER BY started_at DESC, trace_id DESC))[1]
        FROM traces
        WHERE session_id IS NOT NULL
        GROUP BY project_id, coalesce(environment, ''), session_id
        ON CONFLICT (project_id, environment, session_id) DO NOTHING
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION spanlight_sessions_summary_upsert() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            env text := coalesce(NEW.environment, '');
        BEGIN
            INSERT INTO sessions_summary (
                project_id, environment, session_id, first_at, last_at,
                trace_count, error_count, cost_usd, input_tokens, output_tokens,
                has_unpriced, external_user_id, multi_user, last_trace_id
            ) VALUES (
                NEW.project_id, env, NEW.session_id, NEW.started_at, NEW.ended_at,
                1, coalesce(NEW.error_count, 0), coalesce(NEW.cost_usd, 0),
                coalesce(NEW.input_tokens, 0), coalesce(NEW.output_tokens, 0),
                coalesce(NEW.has_unpriced, false), NEW.external_user_id, false, NEW.trace_id
            )
            ON CONFLICT (project_id, environment, session_id) DO UPDATE SET
                first_at = LEAST(sessions_summary.first_at, EXCLUDED.first_at),
                last_at = GREATEST(sessions_summary.last_at, EXCLUDED.last_at),
                trace_count = sessions_summary.trace_count + 1,
                error_count = sessions_summary.error_count + EXCLUDED.error_count,
                cost_usd = sessions_summary.cost_usd + EXCLUDED.cost_usd,
                input_tokens = sessions_summary.input_tokens + EXCLUDED.input_tokens,
                output_tokens = sessions_summary.output_tokens + EXCLUDED.output_tokens,
                has_unpriced = sessions_summary.has_unpriced OR EXCLUDED.has_unpriced,
                multi_user = sessions_summary.multi_user OR (
                    sessions_summary.external_user_id IS NOT NULL
                    AND EXCLUDED.external_user_id IS NOT NULL
                    AND sessions_summary.external_user_id <> EXCLUDED.external_user_id
                ),
                external_user_id = CASE
                    WHEN sessions_summary.multi_user OR (
                        sessions_summary.external_user_id IS NOT NULL
                        AND EXCLUDED.external_user_id IS NOT NULL
                        AND sessions_summary.external_user_id <> EXCLUDED.external_user_id
                    ) THEN NULL
                    ELSE coalesce(sessions_summary.external_user_id, EXCLUDED.external_user_id)
                END,
                last_trace_id = CASE
                    WHEN EXCLUDED.last_at >= sessions_summary.last_at THEN EXCLUDED.last_trace_id
                    ELSE sessions_summary.last_trace_id
                END;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER traces_sessions_summary_insert
        AFTER INSERT ON traces
        FOR EACH ROW
        WHEN (NEW.session_id IS NOT NULL)
        EXECUTE FUNCTION spanlight_sessions_summary_upsert()
        """
    )

    with op.get_context().autocommit_block():
        _create_concurrent_index(
            TRACES_SESSION_ENV_INDEX,
            f"CREATE INDEX CONCURRENTLY {TRACES_SESSION_ENV_INDEX} "
            "ON traces (project_id, environment, started_at DESC) "
            "WHERE session_id IS NOT NULL",
        )
        _create_concurrent_index(
            SPANS_AGENT_TRGM_INDEX,
            f"CREATE INDEX CONCURRENTLY {SPANS_AGENT_TRGM_INDEX} ON spans "
            "USING gin (spanlight_search_text(input, output) gin_trgm_ops) "
            "WHERE kind IN ('llm', 'tool', 'agent')",
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {SPANS_AGENT_TRGM_INDEX}")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {TRACES_SESSION_ENV_INDEX}")
    op.execute("DROP TRIGGER IF EXISTS traces_sessions_summary_insert ON traces")
    op.execute("DROP FUNCTION IF EXISTS spanlight_sessions_summary_upsert()")
    op.execute("DROP TABLE IF EXISTS sessions_summary")

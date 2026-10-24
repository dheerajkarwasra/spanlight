-- Spans with payloads for measuring the trace list previews (`GET /traces?preview=true`) with
-- EXPLAIN. Run after `seed_trace_sorts.sql`, on the same scratch database, as its owner:
--   psql "$URL" -v ON_ERROR_STOP=1 -f backend/load/seed_trace_previews.sql
--
-- The newest 5 000 traces of the measured project (100 pages) get four spans each: a chain root
-- and three llm calls under it. Half of the roots carry their own short input and output, so
-- the other half make the preview read the llm calls. Every llm input is a chat request with a
-- system prompt of about 2.4 KB; one trace in ten has a first call with a 300 KB random hex
-- image (incompressible enough that TOAST cannot shrink it much), which the preview still reads
-- whole before cutting it to 4000 characters.

BEGIN;

SET LOCAL app.bypass_rls = 'on';

INSERT INTO spans (
    project_id, trace_id, span_id, parent_span_id, kind, name, status, started_at, ended_at,
    provider, model, input, output, attributes, payload_bytes
)
SELECT
    t.project_id,
    t.trace_id,
    left(md5(t.trace_id || k::text), 16),
    CASE WHEN k = 0 THEN NULL ELSE left(md5(t.trace_id || '0'), 16) END,
    (CASE WHEN k = 0 THEN 'chain' ELSE 'llm' END)::span_kind,
    CASE WHEN k = 0 THEN 'agent' ELSE 'chat gpt-4o-mini' END,
    'ok',
    t.started_at + k * interval '1 ms',
    greatest(t.ended_at, t.started_at + k * interval '1 ms'),
    CASE WHEN k > 0 THEN 'openai' END,
    CASE WHEN k > 0 THEN 'gpt-4o-mini' END,
    CASE
        WHEN k = 0 AND t.n % 2 = 0 THEN to_jsonb('What changed in the release notes for ' || t.n || '?')
        WHEN k = 0 THEN NULL
        ELSE jsonb_build_object(
            'model', 'gpt-4o-mini',
            'messages', jsonb_build_array(
                jsonb_build_object('role', 'system', 'content', repeat('You are a careful assistant. ', 85)),
                jsonb_build_object(
                    'role', 'user',
                    'content', CASE
                        WHEN k = 1 AND t.n % 10 = 1 THEN 'Describe data:image/png;base64,' || t.image
                        ELSE 'Question ' || t.n || ', step ' || k
                    END
                )
            )
        )
    END,
    CASE
        WHEN k = 0 AND t.n % 2 = 0 THEN to_jsonb('The release notes list ' || t.n || ' changes.')
        WHEN k = 0 THEN NULL
        ELSE jsonb_build_object('role', 'assistant', 'content', repeat('answer text ', 60))
    END,
    '{}'::jsonb,
    NULL
FROM (
    SELECT
        recent.*,
        CASE WHEN recent.n % 10 = 1 THEN (
            SELECT string_agg(md5(random()::text || g::text), '') FROM generate_series(1, 9400) AS g
            WHERE recent.n IS NOT NULL
        ) END AS image
    FROM (
        SELECT project_id, trace_id, started_at, ended_at,
               row_number() OVER (ORDER BY started_at DESC, trace_id DESC) AS n
        FROM traces
        WHERE project_id = '00000000-0000-0000-0000-0000000000b1'
        ORDER BY started_at DESC, trace_id DESC
        LIMIT 5000
    ) AS recent
) AS t
CROSS JOIN generate_series(0, 3) AS k;

COMMIT;

VACUUM ANALYZE spans;

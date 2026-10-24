-- Trace rows for measuring the trace list sorts (duration, cost, tokens, retry cost) with
-- EXPLAIN. Writes only `traces`, straight in SQL, which is far faster than `seed.py` and is all
-- these sorts read; the spans, rollups and keys of a full load seed are not needed.
--
-- One measured project with 300 000 traces (10 000 a day, about `seed.py`'s realistic profile)
-- over the 30 days before `now()`, stored oldest first so rows that started close together sit
-- close together, and a second project with 50 000 traces so row-level security has another
-- tenant to filter out. Unknown values at realistic rates: about 8.5 % of the costs (an
-- unpriced model on a trace with an LLM call), about 5.8 % of the retry costs (not recomputed,
-- or a retry of an unpriced call), and 15 % of the traces without tokens (no LLM call).
--
-- Run it as the database owner on a scratch database that `spanlight migrate` has prepared:
--   psql "$URL" -v ON_ERROR_STOP=1 -f backend/load/seed_trace_sorts.sql
-- The measured project is 00000000-0000-0000-0000-0000000000b1.

BEGIN;

INSERT INTO organizations (id, name, slug)
VALUES ('00000000-0000-0000-0000-0000000000a1', 'Sort load', 'sort-load');

INSERT INTO projects (id, org_id, name, slug)
VALUES
    ('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000a1', 'Measured', 'measured'),
    ('00000000-0000-0000-0000-0000000000b2', '00000000-0000-0000-0000-0000000000a1', 'Other', 'other');

-- The owner seeds every project, so the bypass flag lets the rows past the forced policy.
SET LOCAL app.bypass_rls = 'on';

INSERT INTO traces (
    project_id, trace_id, name, environment, started_at, ended_at, span_count, error_count,
    input_tokens, output_tokens, cost_usd, has_unpriced, retry_calls, retry_cost_usd
)
SELECT
    project_id,
    md5(project_id::text || i::text),
    (ARRAY['answer', 'summarize', 'classify', 'agent run', 'search'])[1 + i % 5],
    CASE WHEN i % 10 = 0 THEN 'staging' ELSE 'prod' END,
    started_at,
    -- Durations from 1 ms to about 3 minutes, most under a few seconds, microsecond precision.
    started_at + make_interval(secs => round((exp(random() * 12) / 1000)::numeric, 6)),
    1 + i % 6,
    CASE WHEN random() < 0.02 THEN 1 ELSE 0 END,
    CASE WHEN no_llm THEN 0 ELSE (random() * 8000)::bigint END,
    CASE WHEN no_llm THEN 0 ELSE (random() * 1500)::bigint END,
    CASE WHEN no_llm THEN 0 WHEN unpriced THEN NULL ELSE round((random() * 0.05)::numeric, 8) END,
    unpriced,
    CASE WHEN not_recomputed THEN NULL WHEN retried THEN 1 ELSE 0 END,
    CASE
        WHEN not_recomputed OR (retried AND unpriced) THEN NULL
        WHEN retried THEN round((random() * 0.02)::numeric, 8)
        ELSE 0
    END
FROM (
    SELECT
        i,
        CASE WHEN i <= 300000 THEN '00000000-0000-0000-0000-0000000000b1'::uuid
             ELSE '00000000-0000-0000-0000-0000000000b2'::uuid END AS project_id,
        now() - interval '30 days'
            + (CASE WHEN i <= 300000 THEN i / 300000.0 ELSE (i - 300000) / 50000.0 END)
              * interval '30 days' AS started_at,
        random() < 0.15 AS no_llm,
        random() < 0.10 AS unpriced,
        random() < 0.05 AS not_recomputed,
        random() < 0.08 AS retried
    FROM generate_series(1, 350000) AS i
) AS rows
ORDER BY started_at;

COMMIT;

VACUUM ANALYZE traces;

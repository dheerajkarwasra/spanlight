# 3. Tenant isolation is enforced by Postgres row-level security

Date: 2026-10-07 · Status: accepted

## Context

Traces contain customers' prompts and completions. A single missing `WHERE project_id = …` in application code would leak one tenant's data to another.

## Decision

Authorization is checked in three layers:

1. Middleware identifies the caller (session or API key).
2. A route dependency, `require(permission)`, checks membership and role through one central permission table. Non-members get `404`, so resource existence is not revealed.
3. `traces` and `spans` have `ENABLE` and `FORCE ROW LEVEL SECURITY`, with a policy comparing `project_id` to `current_setting('app.project_id')`. Each request sets it with `SET LOCAL` inside its transaction. Background maintenance that spans projects uses an explicit bypass setting that only worker code sets.

The application connects as a non-superuser role, because superusers bypass RLS.

## Consequences

A query that forgets its tenant filter returns nothing instead of everything. Tests prove cross-tenant reads fail even through raw SQL. The cost is one `SET LOCAL` per request and slightly more care in migrations.

## Amendment

Amended 2026-10-09: the policies now wrap each `current_setting()` call in `(SELECT …)`, so Postgres evaluates the setting once per statement (an InitPlan) instead of once per row. Which rows are visible is unchanged; migration 0017 made the change because the per-row evaluation was a measurable cost when reading millions of spans.

## Exception: api_keys

`api_keys` has a `project_id` column but no row-level security, by decision (2026-10-12). A key is looked up by its prefix before the request's project is known, because the key is what names the project. A policy would therefore need the bypass setting on every authenticated request, which would turn the bypass from a worker-only escape hatch into a routine one. Instead, every query that lists or changes keys filters by the project resolved from the path, and the tests in `backend/tests/keys/` prove that a key of one project cannot be listed, revoked or used from another. The rows hold key metadata and a SHA-256 hash of the secret, never the secret itself. `backend/tests/rls/test_policy_inventory.py` names `api_keys` as the only project-scoped table without row-level security, so a second exception cannot appear unnoticed.

The alternative considered was a narrow, SELECT-only policy like `gateway_keys_prefix_lookup`, which shows a request the one key whose prefix it presented. It would cover the lookup but not the `last_used_at` update that follows it, and it would put another setting on every authenticated request for a table that holds only key metadata and hashes. The owner decided on 2026-10-12 to keep the exception, with the project filter and the tests above as its guard.

## Request-path reads across projects

Amended 2026-10-12: a request never sets the bypass setting. The one request that needs a figure across an organization's projects, the explain budget's month-to-date spend, calls the database function `explain_month_to_date_cost` (migration 0507). It is `SECURITY DEFINER` with a pinned `search_path`, returns a single sum for the organization the request was authorized for, turns the bypass on for its own query only and restores the caller's value before it returns, and is executable only by the app role. The request's transaction stays bound to its project, so the reservation it then inserts is checked by the table's policy. `backend/tests/rls/test_bypass_callers.py` lists every module that refers to the bypass, fails when one is added outside the modules it allows, and fails when the API imports one of them, directly, through other modules or through a parent package. The allowed modules are job-only, with one exception: `app/db/rls.py`, which defines the setter next to `bind_project` and which every request imports for the latter.

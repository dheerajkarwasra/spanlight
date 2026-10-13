"""Add `explain_month_to_date_cost`, the explain budget's sum, as a narrow database function.

Revision ID: 0507
Revises: 0506
Create Date: 2026-10-12

"Explain with Claude" checks an organization's monthly budget against the cost of every
explanation in every project of the organization. The request that checks it is bound to one
project, and `insight_explanations` is under row-level security, so the sum used to set the
worker's bypass flag inside the request's transaction, where it then also covered the
reservation insert. This function replaces that: the request calls it and stays bound to its
project, so the insert is checked by the table's policy.

Choices worth knowing about:

* `SECURITY DEFINER` with `search_path` pinned to `pg_catalog, pg_temp` (temporary objects
  last) and every table named with its schema, so a caller cannot put its own objects in front
  of the ones the body uses. It runs as the migration role.
* The body sees every project's rows: it turns the bypass flag on around its one query and puts
  the caller's value back before it returns, so the bypass never reaches the caller's
  statements. If the query fails, the caller's transaction (or savepoint) is aborted, and its
  rollback undoes the flag. The flag is set in the body rather than with the function's own
  `SET` clause because only a superuser may attach a custom setting to a function, and the
  migration role need not be one. (Where the owner is a superuser, row-level security does not
  apply to the body anyway.)
* It returns one number: the summed `cost_usd` of the given organization's explanations created
  at or after `since`, reservations included, joined through `projects`; 0 when there are none.
  The organization comes from the caller, which passes the organization of the project the
  request was authorized for.
* `REVOKE ALL ... FROM PUBLIC`: only its owner and the app role, to which
  `spanlight ensure-app-role` grants `EXECUTE` by name (`app.db.roles.APP_FUNCTIONS`), can call
  it.

The migration creates one function. Downgrade drops it; the release that needs it must be rolled
back first.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0507"
down_revision: str | None = "0506"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SIGNATURE = "explain_month_to_date_cost(org uuid, since timestamptz)"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE FUNCTION {SIGNATURE} RETURNS numeric
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
        DECLARE
            caller_bypass text := current_setting('app.bypass_rls', true);
            total numeric;
        BEGIN
            PERFORM set_config('app.bypass_rls', 'on', true);
            SELECT COALESCE(SUM(e.cost_usd), 0) INTO total
            FROM public.insight_explanations AS e
            JOIN public.projects AS p ON p.id = e.project_id
            WHERE p.org_id = explain_month_to_date_cost.org
              AND e.created_at >= explain_month_to_date_cost.since;
            PERFORM set_config('app.bypass_rls', COALESCE(caller_bypass, ''), true);
            RETURN total;
        END
        $$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS explain_month_to_date_cost(uuid, timestamptz)")

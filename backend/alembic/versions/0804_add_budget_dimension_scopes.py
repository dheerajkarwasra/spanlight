"""Add the `metadata` and `trace_name` budget scopes and widen `budgets.scope_id` to 400.

Revision ID: 0804
Revises: 0803
Create Date: 2026-10-16

A budget can now cap the spend of the traces that carry one metadata value (`scope_id` is
`<key>=<value>`, split at the first `=`) or one trace name. A metadata scope is up to
64 + 1 + 256 = 321 characters, past the 200 that 0304 allowed, so the length CHECK is widened to
400.

Choices worth knowing about:

* `ALTER TYPE ... ADD VALUE` runs outside the migration's transaction (`autocommit_block()`): a
  value added inside a transaction cannot be used until it commits, and outside one the
  statement is safe to repeat (`IF NOT EXISTS`). It changes only the catalog.
* 0304 declared the length CHECK on the column without a name, so the name Postgres gave it is
  looked up in `pg_constraint` by its definition at run time, and every CHECK on
  `char_length(scope_id)` is dropped (the new one too, so the step is safe to repeat). The new
  CHECK, `budgets_scope_id_length_check`, is added `NOT VALID` and validated in its own statement
  with the worker bypass, through the 0508 `_validate` block. `budgets` is small.

Downgrade is lossy: it deletes every budget the 0304 schema cannot hold. Postgres cannot remove
an enum value, so the type is built again without the two scopes, and the 200 CHECK comes back;
so the budgets of the two scopes and every budget whose `scope_id` is longer than 200 characters
are deleted first, with the worker bypass so every project's rows are reached, by deleting their
hidden alert rules: each budget, and the rule's states and events, go with its rule
(`ON DELETE CASCADE`). The 0304 CHECK that ties a NULL `scope_id` to the `project` scope compares
the column with the old type, so it is dropped and added back around the column's type change.
The 200 CHECK comes back under the name Postgres gave it. The lock wait on `budgets` and
`alert_rules` is capped at 5 seconds; if it times out, run the downgrade again.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0804"
down_revision: str | None = "0803"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"
NEW_SCOPES = ("metadata", "trace_name")
SCOPES_0304 = ("project", "gateway_key", "user", "model")
LENGTH_CHECK = "budgets_scope_id_length_check"
LENGTH_CHECK_0304 = "budgets_scope_id_check"
SCOPE_ID_CHECK = "budgets_scope_id_for_scope"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for scope in NEW_SCOPES:
            op.execute(f"ALTER TYPE budget_scope ADD VALUE IF NOT EXISTS '{scope}'")
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    _replace_length_check(LENGTH_CHECK, 400)


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    scopes = ", ".join(f"'{scope}'" for scope in NEW_SCOPES)
    _with_bypass(
        "DELETE FROM alert_rules WHERE id IN "
        f"(SELECT rule_id FROM budgets WHERE scope::text IN ({scopes}) "
        "OR char_length(scope_id) > 200);"
    )
    op.execute(f"ALTER TABLE budgets DROP CONSTRAINT {SCOPE_ID_CHECK}")
    op.execute("ALTER TYPE budget_scope RENAME TO budget_scope_0804")
    old_scopes = ", ".join(f"'{scope}'" for scope in SCOPES_0304)
    op.execute(f"CREATE TYPE budget_scope AS ENUM ({old_scopes})")
    op.execute(
        "ALTER TABLE budgets "
        "ALTER COLUMN scope TYPE budget_scope USING scope::text::budget_scope, "
        f"ADD CONSTRAINT {SCOPE_ID_CHECK} CHECK ((scope_id IS NULL) = (scope = 'project'))"
    )
    op.execute("DROP TYPE budget_scope_0804")
    _replace_length_check(LENGTH_CHECK_0304, 200)


def _replace_length_check(name: str, limit: int) -> None:
    """Drop every CHECK on `char_length(scope_id)`, whatever its name, then add and validate one."""
    _drop_length_checks()
    op.execute(
        f"ALTER TABLE budgets ADD CONSTRAINT {name} "
        f"CHECK (char_length(scope_id) BETWEEN 1 AND {limit}) NOT VALID"
    )
    _validate("budgets", name)


def _drop_length_checks() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            found text;
        BEGIN
            FOR found IN
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'public.budgets'::regclass AND contype = 'c'
                  AND position('char_length(scope_id)' IN pg_get_constraintdef(oid)) > 0
            LOOP
                EXECUTE 'ALTER TABLE public.budgets DROP CONSTRAINT ' || quote_ident(found);
            END LOOP;
        END
        $$
        """
    )


# Copied on purpose into each migration that validates: migrations do not import app code or
# each other, so a later change to a shared helper cannot rewrite what a shipped one did.
def _validate(table: str, constraint: str) -> None:
    """Validate `constraint` against every row of `table`, whatever row-level security hides.

    Migrations run as the table owner, and FORCE ROW LEVEL SECURITY applies the policies to it:
    Postgres validates a foreign key with a query that sees only the rows the policies let
    through. (A CHECK is validated by scanning the table, which the policies do not filter; it
    goes through here too, so every validation follows one rule.) The worker bypass is set for
    the statement's own transaction and the caller's value restored afterwards; on an error the
    transaction aborts and discards the setting by itself. One `DO` block, so the setting and the
    validation share a transaction even inside `autocommit_block()`.
    """
    _with_bypass(f"ALTER TABLE {table} VALIDATE CONSTRAINT {constraint};")


# Copied on purpose into each migration that needs it, as `_validate` above.
def _with_bypass(statement: str) -> None:
    """Run `statement` in one `DO` block with the worker bypass set for its transaction only."""
    op.execute(
        f"""
        DO $$
        DECLARE
            previous text := current_setting('app.bypass_rls', true);
        BEGIN
            PERFORM set_config('app.bypass_rls', 'on', true);
            {statement}
            PERFORM set_config('app.bypass_rls', COALESCE(previous, ''), true);
        END
        $$
        """
    )

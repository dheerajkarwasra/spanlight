"""Let a trace's token totals be unknown.

Revision ID: 1016
Revises: 1015
Create Date: 2026-10-24

`traces.input_tokens` and `traces.output_tokens` were `NOT NULL DEFAULT 0`, and the totals
recompute coalesced an empty sum to 0. A trace none of whose calls reported usage (a failed call,
say) then read "0 tokens" in the traces list, the sessions list and exports, where the project
rule is that an unknown value is NULL. Each total is now the sum of the usage the trace's spans
reported (usage rollups left out, as before), and NULL when none of them reported it.

Choices worth knowing about:

* Dropping NOT NULL and the default changes only the catalog; the ALTER takes an ACCESS
  EXCLUSIVE lock for milliseconds, and the migration waits at most 5 seconds for it
  (`lock_timeout`) rather than stall ingestion queued behind the wait. A new trace row is totalled
  in the transaction that inserts it, so the default was never read.
* The stored zeros that stand for "no span reported usage" are set to NULL here, with the same
  rule the recompute now applies. Only rows holding a 0 are candidates, each checked against its
  own spans; it is one set-based statement over `traces`, run past row-level security, that
  takes row locks only (a batch for one of those traces waits for it). Traces with any reported
  usage keep their totals, so replaying a batch afterwards changes nothing.
* While the old app version still runs during a deploy it writes 0 for those traces again, and
  its API fails to serialise a NULL total; the next batch for such a trace after the deploy
  rewrites its totals.

Downgrade turns every NULL total back into 0 and restores `NOT NULL DEFAULT 0`; SET NOT NULL
scans the table under its lock.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1016"
down_revision: str | None = "1015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"

# The spans of trace `t` that report `{column}`, usage rollups left out as the recompute does.
_REPORTED = """EXISTS (
    SELECT 1 FROM spans AS s
    WHERE s.project_id = t.project_id AND s.trace_id = t.trace_id
      AND NOT s.usage_rollup AND s.{column} IS NOT NULL
)"""
_INPUT_UNREPORTED = "t.input_tokens = 0 AND NOT " + _REPORTED.format(column="input_tokens")
_OUTPUT_UNREPORTED = "t.output_tokens = 0 AND NOT " + _REPORTED.format(column="output_tokens")

_TO_NULL = f"""
    UPDATE traces AS t
    SET input_tokens  = CASE WHEN {_INPUT_UNREPORTED} THEN NULL ELSE t.input_tokens END,
        output_tokens = CASE WHEN {_OUTPUT_UNREPORTED} THEN NULL ELSE t.output_tokens END
    WHERE ({_INPUT_UNREPORTED}) OR ({_OUTPUT_UNREPORTED});
"""

_TO_ZERO = """
    UPDATE traces
    SET input_tokens = coalesce(input_tokens, 0), output_tokens = coalesce(output_tokens, 0)
    WHERE input_tokens IS NULL OR output_tokens IS NULL;
"""


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute(
            "ALTER TABLE traces "
            "ALTER COLUMN input_tokens DROP NOT NULL, ALTER COLUMN input_tokens DROP DEFAULT, "
            "ALTER COLUMN output_tokens DROP NOT NULL, ALTER COLUMN output_tokens DROP DEFAULT"
        )
        op.execute("RESET lock_timeout")
        _with_bypass(_TO_NULL)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        _with_bypass(_TO_ZERO)
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute(
            "ALTER TABLE traces "
            "ALTER COLUMN input_tokens SET DEFAULT 0, ALTER COLUMN input_tokens SET NOT NULL, "
            "ALTER COLUMN output_tokens SET DEFAULT 0, ALTER COLUMN output_tokens SET NOT NULL"
        )
        op.execute("RESET lock_timeout")


# Copied on purpose from 1015: migrations do not import app code or each other.
def _with_bypass(statement: str) -> None:
    """Run `statement` in one `DO` block with the worker bypass set for its transaction only (the
    caller's value restored afterwards), so row-level security hides no project's rows."""
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

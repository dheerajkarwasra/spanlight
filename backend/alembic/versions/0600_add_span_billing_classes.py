"""Add the span billing classes (service tier, web search requests, fee) and `server_tool_fees`.

Revision ID: 0600
Revises: 0508
Create Date: 2026-10-15

A provider bills a call by more than its tokens: the service tier changes the token rate (a batch
call costs half, a priority call up to double) and a server-side tool such as web search carries
a per-request fee on top. Neither was stored, so a batch call was over-priced, a priority call
under-priced, and a web search was free. With these columns a stored figure can be explained
line by line and reconciled against the provider's invoice.

`spans.service_tier` is the tier the provider reported, one of `standard`, `batch`, `priority`
and `flex` (`spans_service_tier_check`); NULL when the provider said nothing, which is priced as
standard. `spans.web_search_requests` is the provider's `web_search_requests` count for the call.
`spans.fee_usd` is the non-token part of `cost_usd`, so `cost_usd` keeps its meaning (the whole
cost of the span) and every existing sum of it includes fees without a change; it is NULL when no
fee was priced. All three stay NULL on spans stored before this migration: there is no backfill.

`server_tool_fees` is the global, versioned price list for server-side tools, one row per
provider, tool and effective date, seeded from the pricing snapshot by `spanlight sync-prices`
(`ON CONFLICT DO NOTHING`, like `model_prices`). `provider` is stored lower-case and the CHECK
makes the stored form the only form, as `price_overrides` does. `tool` is limited to the tools
priced today (`web_search`); a new tool widens the CHECK in its own migration. The rate is
`numeric(12, 6)` dollars per 1,000 requests, not negative, as the sibling rates in `model_prices`
are.

Choices worth knowing about:

* The `spans` steps run outside the migration's transaction (`autocommit_block()`), each
  statement committing on its own, as 0508 does for `spans`. Adding the columns and the
  `NOT VALID` constraints changes only the catalog, under an ACCESS EXCLUSIVE lock held for
  milliseconds; the migration waits at most 5 seconds for it (`lock_timeout`) rather than stall
  every ingest queued behind the wait. Each `VALIDATE CONSTRAINT` then scans the table in its own
  statement under SHARE UPDATE EXCLUSIVE, which lets ingestion and reads continue.
* Every `spans` step is safe to repeat (`IF NOT EXISTS`, drop-then-add of the constraints), so
  running the migration again after an interrupted run is safe. The `spans` steps come first:
  `autocommit_block()` commits what precedes it, so the table create runs afterwards, in the
  transaction that also stamps the revision, and either both land or neither does.
* No new index: the reconciliation report reads the daily cost rollups, not spans.

Downgrade drops the three checks and columns under the same lock timeout, then the table, so the
schema matches 0508 exactly. The fee rows are seed data and come back with the next
`spanlight sync-prices`.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0600"
down_revision: str | None = "0508"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"
SPAN_CHECKS = {
    "spans_service_tier_check": "service_tier IN ('standard', 'batch', 'priority', 'flex')",
    "spans_web_search_requests_check": "web_search_requests >= 0",
    "spans_fee_usd_check": "fee_usd >= 0",
}


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute(
            "ALTER TABLE spans ADD COLUMN IF NOT EXISTS service_tier text, "
            "ADD COLUMN IF NOT EXISTS web_search_requests integer, "
            "ADD COLUMN IF NOT EXISTS fee_usd numeric(14, 8)"
        )
        drops = ", ".join(f"DROP CONSTRAINT IF EXISTS {name}" for name in SPAN_CHECKS)
        op.execute(f"ALTER TABLE spans {drops}")
        adds = ", ".join(
            f"ADD CONSTRAINT {name} CHECK ({expression}) NOT VALID"
            for name, expression in SPAN_CHECKS.items()
        )
        op.execute(f"ALTER TABLE spans {adds}")
        op.execute("RESET lock_timeout")
        for name in SPAN_CHECKS:
            _validate("spans", name)
    op.execute(
        """
        CREATE TABLE server_tool_fees (
            id                   uuid PRIMARY KEY,
            provider             text NOT NULL
                CONSTRAINT server_tool_fees_provider_check CHECK (provider = lower(provider)),
            tool                 text NOT NULL
                CONSTRAINT server_tool_fees_tool_check CHECK (tool IN ('web_search')),
            usd_per_1k_requests  numeric(12, 6) NOT NULL
                CONSTRAINT server_tool_fees_usd_per_1k_requests_check
                CHECK (usd_per_1k_requests >= 0),
            effective_from       timestamptz NOT NULL,
            version              text NOT NULL,
            CONSTRAINT server_tool_fees_provider_tool_effective_from_key
                UNIQUE (provider, tool, effective_from)
        )
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
    op.execute(
        f"""
        DO $$
        DECLARE
            previous text := current_setting('app.bypass_rls', true);
        BEGIN
            PERFORM set_config('app.bypass_rls', 'on', true);
            ALTER TABLE {table} VALIDATE CONSTRAINT {constraint};
            PERFORM set_config('app.bypass_rls', COALESCE(previous, ''), true);
        END
        $$
        """
    )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        drops = ", ".join(f"DROP CONSTRAINT IF EXISTS {name}" for name in SPAN_CHECKS)
        op.execute(
            f"ALTER TABLE spans {drops}, DROP COLUMN IF EXISTS fee_usd, "
            "DROP COLUMN IF EXISTS web_search_requests, DROP COLUMN IF EXISTS service_tier"
        )
        op.execute("RESET lock_timeout")
    op.execute("DROP TABLE IF EXISTS server_tool_fees")

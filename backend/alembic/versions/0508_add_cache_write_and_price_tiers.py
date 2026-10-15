"""Add cache-write usage to spans and cache-write rates with prompt-size tiers to the price tables.

Revision ID: 0508
Revises: 0507
Create Date: 2026-10-15

Anthropic bills prompt-cache writes above the input rate (1.25x for a 5 minute cache, 2x for
1 hour), and bills some models at a higher rate once a prompt passes a size threshold (the tier
shipped here is Claude Haiku 5.5 over 100,000 prompt tokens). Neither was stored, so a cached or
long-prompt call was under-priced.

`spans.cache_write_tokens` is the number of prompt tokens written to the cache in the call, every
lifetime included; `spans.cache_write_1h_tokens` is the part of it written with the 1 hour
lifetime, so it can never exceed the total (`ck_spans_cache_write_1h_le_total`). Both stay NULL
when the provider reported nothing, as the other usage columns do.

`model_prices` and `price_overrides` gain `cache_write_5m_per_mtok` and `cache_write_1h_per_mtok`
(the same `numeric(12, 6)`, not negative, as their sibling rates; NULL means the provider has no
such charge or the rate is unknown) and `min_prompt_tokens`: the smallest prompt the row prices,
`0` for a base price, so a pattern can carry one row per tier. It joins each table's unique key.

Choices worth knowing about:

* The unique constraints keep their names (`model_prices_provider_model_pattern_effective_from_key`,
  `price_overrides_org_model_key`): `app.pricing.overrides_service` names the second in its
  `ON CONFLICT` clause, and the first is the name Postgres gave the key in 0001. Each is dropped
  and recreated in one `ALTER TABLE`, so no statement sees the table without its key. The price
  tables are small (a few hundred rows), so the rebuild and the `NOT NULL DEFAULT 0` column add
  are quick; on Postgres 11+ a constant default rewrites nothing anyway.
* The `spans` steps run outside the migration's transaction (`autocommit_block()`), each
  statement committing on its own, as 0401 and 0501 do for `spans`. Adding the columns and the
  `NOT VALID` constraints changes only the catalog, under an ACCESS EXCLUSIVE lock held for
  milliseconds; the migration waits at most 5 seconds for it (`lock_timeout`) rather than stall
  every ingest queued behind the wait. Each `VALIDATE CONSTRAINT` then scans the table in its own
  statement under SHARE UPDATE EXCLUSIVE, which lets ingestion and reads continue.
* Every `spans` step is safe to repeat (`IF NOT EXISTS`, drop-then-add of the constraints), so
  running the migration again after an interrupted run is safe. The `spans` steps come first:
  `autocommit_block()` commits what precedes it, so the price-table changes run afterwards,
  in the transaction that also stamps the revision, and either both land or neither does.

Downgrade deletes every tier row (`min_prompt_tokens <> 0`) from both price tables, because such a
row would share its key with the base row once the column is gone, then drops the new columns
(their CHECKs go with them) and puts the three- and four-column unique keys back under their
names, so the schema matches 0507 exactly. After a downgrade and a second upgrade, the seeding
skips the seed rows that still exist (`ON CONFLICT DO NOTHING`), so they keep no write rates;
the tier row is inserted again, since its key no longer collides. The seed rows have to be
deleted and `spanlight sync-prices` run for the table to be whole again.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0508"
down_revision: str | None = "0507"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"
SPAN_CHECKS = {
    "spans_cache_write_tokens_check": "cache_write_tokens >= 0",
    "spans_cache_write_1h_tokens_check": "cache_write_1h_tokens >= 0",
    "ck_spans_cache_write_1h_le_total": "cache_write_1h_tokens <= cache_write_tokens",
}
MODEL_PRICES_KEY = "model_prices_provider_model_pattern_effective_from_key"
PRICE_OVERRIDES_KEY = "price_overrides_org_model_key"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        op.execute(
            "ALTER TABLE spans ADD COLUMN IF NOT EXISTS cache_write_tokens integer, "
            "ADD COLUMN IF NOT EXISTS cache_write_1h_tokens integer"
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
    _upgrade_price_table(
        "model_prices", MODEL_PRICES_KEY, "provider, model_pattern, effective_from"
    )
    _upgrade_price_table(
        "price_overrides", PRICE_OVERRIDES_KEY, "org_id, provider, model_pattern, effective_from"
    )


def _upgrade_price_table(table: str, key: str, key_columns: str) -> None:
    op.execute(
        f"""
        ALTER TABLE {table}
            ADD COLUMN cache_write_5m_per_mtok numeric(12, 6)
                CONSTRAINT {table}_cache_write_5m_per_mtok_check
                CHECK (cache_write_5m_per_mtok >= 0),
            ADD COLUMN cache_write_1h_per_mtok numeric(12, 6)
                CONSTRAINT {table}_cache_write_1h_per_mtok_check
                CHECK (cache_write_1h_per_mtok >= 0),
            ADD COLUMN min_prompt_tokens integer NOT NULL DEFAULT 0
                CONSTRAINT {table}_min_prompt_tokens_check CHECK (min_prompt_tokens >= 0),
            DROP CONSTRAINT {key},
            ADD CONSTRAINT {key} UNIQUE ({key_columns}, min_prompt_tokens)
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
            f"ALTER TABLE spans {drops}, DROP COLUMN IF EXISTS cache_write_1h_tokens, "
            "DROP COLUMN IF EXISTS cache_write_tokens"
        )
        op.execute("RESET lock_timeout")
    _downgrade_price_table(
        "price_overrides", PRICE_OVERRIDES_KEY, "org_id, provider, model_pattern, effective_from"
    )
    _downgrade_price_table(
        "model_prices", MODEL_PRICES_KEY, "provider, model_pattern, effective_from"
    )


def _downgrade_price_table(table: str, key: str, key_columns: str) -> None:
    # Rows of a higher tier share their key with the base row once `min_prompt_tokens` is gone,
    # so they are dropped first; the release that wrote them must be rolled back before this.
    op.execute(f"DELETE FROM {table} WHERE min_prompt_tokens <> 0")
    op.execute(
        f"""
        ALTER TABLE {table}
            DROP CONSTRAINT {key},
            DROP COLUMN cache_write_5m_per_mtok,
            DROP COLUMN cache_write_1h_per_mtok,
            DROP COLUMN min_prompt_tokens,
            ADD CONSTRAINT {key} UNIQUE ({key_columns})
        """
    )

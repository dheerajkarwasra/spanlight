"""Add per-organization price overrides.

Revision ID: 0205
Revises: 0204
Create Date: 2026-10-09

An override is an organization's own rate for a model, for negotiated discounts, self-hosted
models and providers the seed table does not list. At ingestion the organization's overrides are
loaded next to the seed prices and win over a seed row for the same provider and pattern; no
other organization ever sees them.

Choices worth knowing about:

* The money columns match `model_prices` (`numeric(12, 6)`, not negative), so a rate means the
  same thing in both tables.
* `provider` and `model_pattern` are stored lower-case, as the seed table does. The CHECKs make
  the stored form the only form. (The price lookup lower-cased the model when this was written;
  since 2026-10-13 it matches the model case-sensitively, so an override prices lower-case
  model names only.)
* The table is scoped to an organization, not a project, so it has no row-level security: every
  query filters by `org_id` (`app.pricing.queries`). `org_id` cascades, so deleting an
  organization deletes its overrides.
* `UNIQUE (org_id, provider, model_pattern, effective_from)` makes a duplicate a
  `409 PRICE_OVERRIDE_EXISTS`. Its index leads with `org_id`, so it also serves the foreign key
  and the per-organization read at ingestion; a separate `org_id` index would duplicate it.
* `created_by` is `ON DELETE SET NULL`: removing a person keeps the overrides they added.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0205"
down_revision: str | None = "0204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE price_overrides (
            id                    uuid PRIMARY KEY,
            org_id                uuid NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
            provider              text NOT NULL
                CHECK (char_length(provider) BETWEEN 1 AND 100 AND provider = lower(provider)),
            model_pattern         text NOT NULL
                CHECK (
                    char_length(model_pattern) BETWEEN 1 AND 200
                    AND model_pattern = lower(model_pattern)
                ),
            input_per_mtok        numeric(12, 6) NOT NULL CHECK (input_per_mtok >= 0),
            output_per_mtok       numeric(12, 6) NOT NULL CHECK (output_per_mtok >= 0),
            cached_input_per_mtok numeric(12, 6) CHECK (cached_input_per_mtok >= 0),
            effective_from        timestamptz NOT NULL,
            created_by            uuid REFERENCES users (id) ON DELETE SET NULL,
            created_at            timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT price_overrides_org_model_key
                UNIQUE (org_id, provider, model_pattern, effective_from)
        )
        """
    )
    op.execute("CREATE INDEX price_overrides_created_by_idx ON price_overrides (created_by)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS price_overrides")

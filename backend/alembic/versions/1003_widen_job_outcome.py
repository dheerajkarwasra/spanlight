"""Widen `jobs.outcome` with `already_selected`.

Revision ID: 1003
Revises: 1002
Create Date: 2026-10-17

The nightly review selection (`select_for_review_project`) ends `done` / `already_selected` when
its project-day already has a `review_selections` row: nothing was queued, and retrying cannot
change that. `0013` lists the allowed outcomes in `jobs_outcome_check`, so the new value needs
the constraint rebuilt. The constraint is dropped and added `NOT VALID`, then validated on its
own, so a large jobs table is not locked against writes while it is scanned.

Downgrade restores the `0013` list; a row that holds `already_selected` makes the validation
fail, so it is cleared first.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1003"
down_revision: str | None = "1002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PREVIOUS = ("ok", "skipped_not_configured", "skipped_budget")
OUTCOMES = (*PREVIOUS, "already_selected")


def _set_outcomes(values: tuple[str, ...]) -> None:
    allowed = ", ".join(f"'{value}'" for value in values)
    op.execute("ALTER TABLE jobs DROP CONSTRAINT jobs_outcome_check")
    op.execute(
        f"ALTER TABLE jobs ADD CONSTRAINT jobs_outcome_check "
        f"CHECK (outcome IN ({allowed})) NOT VALID"
    )
    op.execute("ALTER TABLE jobs VALIDATE CONSTRAINT jobs_outcome_check")


def upgrade() -> None:
    _set_outcomes(OUTCOMES)


def downgrade() -> None:
    op.execute("UPDATE jobs SET outcome = NULL WHERE outcome = 'already_selected'")
    _set_outcomes(PREVIOUS)

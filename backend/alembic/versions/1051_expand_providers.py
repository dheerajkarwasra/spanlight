"""Widen credential, billing and provider status constraints.

Enum labels are added in autocommit so the checks can reference them. Replacing the
checks takes bounded exclusive locks without rescanning the existing rows. Downgrade
refuses new-provider rows and leaves the unused enum labels in place.

Revision ID: 1051
Revises: 1050
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1051"
down_revision: str | None = "1050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_PROVIDERS = ("azure_openai", "mistral", "groq", "deepseek", "xai", "together")
PREVIOUS_CREDENTIALS = ("openai", "anthropic", "openai_compatible", "gemini")
PREVIOUS_BILLING = ("anthropic", "openai", "google")
NEW_STATUS_PROVIDERS = ("mistral", "groq", "deepseek", "together")
CREDENTIAL_CONSTRAINT = "provider_credentials_base_url_matches_provider"


def _listed(providers: tuple[str, ...]) -> str:
    return ", ".join(f"'{provider}'" for provider in providers)


def _replace_check(table: str, constraint: str, expression: str) -> None:
    # The previous checks imply the widened ones, so no upgrade-time table scan is needed.
    # On downgrade, the preflight checks the rows while holding the same table locks.
    op.execute(
        f"ALTER TABLE {table} DROP CONSTRAINT {constraint}, "
        f"ADD CONSTRAINT {constraint} CHECK ({expression}) NOT VALID"
    )


def _set_billing_and_status(*, expanded: bool) -> None:
    billing = (*PREVIOUS_BILLING, *NEW_PROVIDERS) if expanded else PREVIOUS_BILLING
    status = (*PREVIOUS_BILLING, *NEW_STATUS_PROVIDERS) if expanded else PREVIOUS_BILLING
    _replace_check(
        "billing_sources", "billing_sources_provider_check", f"provider IN ({_listed(billing)})"
    )
    for table in ("provider_incidents", "provider_status_sources"):
        _replace_check(table, f"{table}_provider_check", f"provider IN ({_listed(status)})")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for provider in NEW_PROVIDERS:
            op.execute(f"ALTER TYPE provider_kind ADD VALUE IF NOT EXISTS '{provider}'")

    op.execute("SET LOCAL lock_timeout = '5s'")
    _replace_check(
        "provider_credentials",
        CREDENTIAL_CONSTRAINT,
        "(provider IN ('openai_compatible', 'azure_openai')) = (base_url IS NOT NULL)",
    )
    _set_billing_and_status(expanded=True)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "LOCK TABLE provider_credentials, billing_sources, provider_incidents, "
        "provider_status_sources IN ACCESS EXCLUSIVE MODE"
    )
    providers = _listed(NEW_PROVIDERS)
    status = _listed(NEW_STATUS_PROVIDERS)
    op.execute(
        f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM provider_credentials WHERE provider IN ({providers}))
                OR EXISTS (SELECT 1 FROM billing_sources WHERE provider IN ({providers}))
                OR EXISTS (SELECT 1 FROM provider_incidents WHERE provider IN ({status}))
                OR EXISTS (SELECT 1 FROM provider_status_sources WHERE provider IN ({status}))
            THEN
                RAISE EXCEPTION 'Cannot downgrade 1051 while new provider rows exist'
                    USING HINT = 'Migrate or archive those rows before retrying the downgrade.';
            END IF;
        END $$;
        """
    )
    # PostgreSQL cannot remove individual enum labels. Restrict writes to the previous values.
    _replace_check(
        "provider_credentials",
        CREDENTIAL_CONSTRAINT,
        f"provider IN ({_listed(PREVIOUS_CREDENTIALS)}) AND "
        "((provider = 'openai_compatible') = (base_url IS NOT NULL))",
    )
    _set_billing_and_status(expanded=False)

"""Register Mistral and Groq's verified public status feeds.

The upgrade inserts two source rows. A downgrade removes those registrations and leaves
incident history intact.

Revision ID: 1052
Revises: 1051
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1052"
down_revision: str | None = "1051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCES = """
    ('mistral', 'https://status.mistral.ai/api/v2/incidents.json'),
    ('groq', 'https://groqstatus.com/api/v2/incidents.json')
"""


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        f"INSERT INTO provider_status_sources (provider, url) VALUES {SOURCES} "
        "ON CONFLICT (provider) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(f"DELETE FROM provider_status_sources WHERE (provider, url) IN ({SOURCES})")

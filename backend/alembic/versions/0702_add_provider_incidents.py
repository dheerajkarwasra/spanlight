"""Add the providers' published status incidents and the state of each status feed.

Revision ID: 0702
Revises: 0701
Create Date: 2026-10-16

`provider_incidents` keeps the incidents Anthropic, OpenAI and Google publish on their status
pages, polled by the worker, so the provider health page can lay them over a project's own
errors. `provider_status_sources` holds one row per feed: its fixed URL, the conditional request
validators (`etag`, `last_modified`) and how the last poll went.

Choices worth knowing about:

* Both tables are global: they hold public data about the providers and nothing about any
  tenant, so they carry no `project_id`, `org_id`, `user_id` or `key_id` and no row-level
  security (`GLOBAL_TABLES` in `tests/rls/test_policy_inventory.py`).
* `UNIQUE (provider, external_id)` is the feed's own identity of an incident; the poller upserts
  on it. `(provider, started_at DESC)` serves one provider's list, `(started_at DESC)` the
  overlay that reads a window across providers.
* The caps the adapters apply are checked here too: title 1 to 500 characters, latest update at
  most 2000, at most 20 components, `https` URLs only.
* `provider_status_sources` is seeded with the three feeds (`ON CONFLICT DO NOTHING`, so a rerun
  keeps their polling state). The URLs are constants of the adapters, never settings.

Two new tables in one transaction; the 5 second lock wait cap is kept for uniformity. Downgrade
drops both tables, and the incidents with them.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0702"
down_revision: str | None = "0701"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCK_TIMEOUT = "5s"
PROVIDERS = "'anthropic', 'openai', 'google'"
SOURCES = (
    ("anthropic", "https://status.claude.com/api/v2/incidents.json"),
    ("openai", "https://status.openai.com/api/v2/incidents.json"),
    ("google", "https://status.cloud.google.com/incidents.json"),
)


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    _create_incidents()
    _create_sources()
    values = ", ".join(f"('{provider}', '{url}')" for provider, url in SOURCES)
    op.execute(
        f"INSERT INTO provider_status_sources (provider, url) VALUES {values} "
        "ON CONFLICT (provider) DO NOTHING"
    )


def _create_incidents() -> None:
    op.execute(
        f"""
        CREATE TABLE provider_incidents (
            id               uuid PRIMARY KEY,
            provider         text NOT NULL
                CONSTRAINT provider_incidents_provider_check CHECK (provider IN ({PROVIDERS})),
            source           text NOT NULL
                CONSTRAINT provider_incidents_source_check CHECK (source IN
                    ('statuspage_json', 'incidentio_json', 'incidentio_rss', 'gcp_json')),
            external_id      text NOT NULL,
            title            text NOT NULL
                CONSTRAINT provider_incidents_title_check
                CHECK (char_length(title) BETWEEN 1 AND 500),
            impact           text NOT NULL
                CONSTRAINT provider_incidents_impact_check
                CHECK (impact IN ('none', 'minor', 'major', 'critical', 'unknown')),
            status           text NOT NULL
                CONSTRAINT provider_incidents_status_check CHECK (status IN
                    ('investigating', 'identified', 'monitoring', 'resolved', 'postmortem',
                     'unknown')),
            api_affected     boolean NOT NULL,
            components       text[] NOT NULL DEFAULT '{{}}'
                CONSTRAINT provider_incidents_components_check
                CHECK (cardinality(components) <= 20),
            started_at       timestamptz NOT NULL,
            resolved_at      timestamptz,
            url              text NOT NULL
                CONSTRAINT provider_incidents_url_check CHECK (url LIKE 'https://%'),
            latest_update    text
                CONSTRAINT provider_incidents_latest_update_check
                CHECK (char_length(latest_update) <= 2000),
            feed_updated_at  timestamptz,
            first_seen_at    timestamptz NOT NULL DEFAULT now(),
            fetched_at       timestamptz NOT NULL,
            CONSTRAINT provider_incidents_provider_external_id_key
                UNIQUE (provider, external_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX provider_incidents_provider_started_idx "
        "ON provider_incidents (provider, started_at DESC)"
    )
    op.execute(
        "CREATE INDEX provider_incidents_started_idx ON provider_incidents (started_at DESC)"
    )


def _create_sources() -> None:
    op.execute(
        f"""
        CREATE TABLE provider_status_sources (
            provider              text PRIMARY KEY
                CONSTRAINT provider_status_sources_provider_check
                CHECK (provider IN ({PROVIDERS})),
            url                   text NOT NULL
                CONSTRAINT provider_status_sources_url_check CHECK (url LIKE 'https://%'),
            etag                  text,
            last_modified         text,
            last_polled_at        timestamptz,
            last_success_at       timestamptz,
            last_outcome          text
                CONSTRAINT provider_status_sources_last_outcome_check CHECK (last_outcome IN
                    ('fetched', 'not_modified', 'http_error', 'parse_error', 'too_large',
                     'timeout', 'blocked')),
            consecutive_failures  integer NOT NULL DEFAULT 0
                CONSTRAINT provider_status_sources_consecutive_failures_check
                CHECK (consecutive_failures >= 0),
            last_error_type       text
        )
        """
    )


def downgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    op.execute("DROP TABLE IF EXISTS provider_status_sources")
    op.execute("DROP TABLE IF EXISTS provider_incidents")

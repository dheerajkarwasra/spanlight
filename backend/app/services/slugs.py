"""URL slugs derived from display names."""

import re
import secrets

# Org slugs become the first URL segment of the dashboard (`/<orgSlug>/<projectSlug>/...`), so an
# org must never be given a slug that is also a top-level path: the dashboard's own pages and the
# paths the web server hands to the backend. This list must match `RESERVED_FIRST_SEGMENTS` in
# `frontend/src/features/shell/slug-map.ts`, which keeps id URLs for any org that already holds
# one of these slugs; `tests/orgs/test_reserved_slugs.py` fails when the two drift apart.
RESERVED_ORG_SLUGS: frozenset[str] = frozenset(
    {
        "account",
        "api",
        "assets",
        "docs",
        "forgot-password",
        "gw",
        "health",
        "invite",
        "login",
        "onboarding",
        "reset-password",
        "signup",
        "v1",
        "verify-email",
    }
)

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(name: str, *, fallback: str) -> str:
    slug = _NON_SLUG.sub("-", name.lower()).strip("-")[:48].strip("-")
    return slug or fallback


def with_random_suffix(slug: str) -> str:
    return f"{slug}-{secrets.token_hex(3)}"

"""The request rules of a billing source that depend on its kind (spec §8.2). Pure.

An API kind fixes the provider and needs an admin key of that provider's shape; a CSV source
names its provider and holds no key. A rule that fails raises `InvalidSourceField`, whose
message never repeats the key.
"""

from collections.abc import Mapping

from pydantic import SecretStr

from app.db.models.billing import BillingProvider, BillingSourceKind
from app.reconciliation.errors import InvalidSourceField

PROVIDER_OF_KIND: Mapping[BillingSourceKind, BillingProvider] = {
    "anthropic_admin_api": "anthropic",
    "openai_costs_api": "openai",
}
ADMIN_KEY_PREFIXES: Mapping[BillingSourceKind, str] = {
    "anthropic_admin_api": "sk-ant-admin",
    "openai_costs_api": "sk-admin",
}
MAX_ADMIN_KEY_LENGTH = 512
NOT_ADMIN_KEY = "does not look like an Admin API key"


def is_api_kind(kind: BillingSourceKind) -> bool:
    """Whether the source fetches from a provider's API, with a sealed admin key."""
    return kind in PROVIDER_OF_KIND


def provider_for(kind: BillingSourceKind, sent: BillingProvider | None) -> BillingProvider:
    """The source's provider: the kind's own for an API kind, the one sent for a CSV source."""
    derived = PROVIDER_OF_KIND.get(kind)
    if derived is None:
        if sent is None:
            raise InvalidSourceField("provider", "is required for a CSV source")
        return sent
    if sent is not None and sent != derived:
        raise InvalidSourceField("provider", f"must be {derived} for {kind}, or left out")
    return derived


def clean_admin_key(kind: BillingSourceKind, sent: SecretStr | None) -> str | None:
    """The admin key of a new source, checked against its kind; None for a CSV source."""
    if not is_api_kind(kind):
        if sent is not None:
            raise InvalidSourceField("admin_key", "must be left out for a CSV source")
        return None
    if sent is None:
        raise InvalidSourceField("admin_key", "is required for a source that fetches from an API")
    return admin_key_for(kind, sent)


def admin_key_for(kind: BillingSourceKind, sent: SecretStr) -> str:
    """The admin key trimmed, if it has the shape of `kind`'s keys.

    A key pasted from a terminal often carries a trailing newline, so the ends are trimmed.
    Anything but visible ASCII inside could not go in an HTTP header and is no admin key.
    """
    key = sent.get_secret_value().strip()
    if len(key) > MAX_ADMIN_KEY_LENGTH:
        raise InvalidSourceField("admin_key", f"must be at most {MAX_ADMIN_KEY_LENGTH} characters")
    prefix = ADMIN_KEY_PREFIXES[kind]
    if not key.startswith(prefix) or not all("!" <= character <= "~" for character in key):
        raise InvalidSourceField("admin_key", NOT_ADMIN_KEY)
    return key

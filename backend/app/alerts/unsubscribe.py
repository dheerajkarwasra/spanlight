"""Unsubscribe links for the weekly digest.

A token is `<user id as 32 hex digits>.<HMAC-SHA256 over "digest-unsubscribe:" + user id, keyed by
SECRET_KEY, base64url>`. It names one user and does one thing, so it is not stored and never
expires: a link in a digest from last year still works. The purpose prefix keeps it from ever
validating as another signed value, and rotating SECRET_KEY retires every link at once.
"""

import base64
import hashlib
import hmac
import uuid

from app.config import Settings

UNSUBSCRIBE_PATH = "/api/v1/email/unsubscribe"
_PURPOSE = "digest-unsubscribe:"
# 32 hex digits, a dot and 43 base64url characters; anything longer is not a token.
_MAX_LENGTH = 76


def unsubscribe_token(user_id: uuid.UUID, settings: Settings) -> str:
    return f"{user_id.hex}.{_signature(user_id, settings)}"


def user_for_unsubscribe_token(token: str, settings: Settings) -> uuid.UUID | None:
    """The user the token was made for, or None for anything that is not a valid token."""
    if len(token) > _MAX_LENGTH:
        return None
    raw_id, _, signature = token.partition(".")
    try:
        user_id = uuid.UUID(hex=raw_id)
    except ValueError:
        return None
    if raw_id != user_id.hex:  # one spelling per user: no braces, dashes or capitals
        return None
    expected = _signature(user_id, settings)
    return user_id if hmac.compare_digest(expected.encode(), signature.encode()) else None


def unsubscribe_links(user_id: uuid.UUID, settings: Settings) -> tuple[str, dict[str, str]]:
    """The footer link of a digest to this user, and its RFC 8058 one-click headers.

    The page link keeps the token in the fragment, out of access logs. The header URL needs it in
    the query: a mail client POSTs to that URL as it is, with no page to read a fragment.
    """
    token = unsubscribe_token(user_id, settings)
    base = settings.app_base_url
    headers = {
        "List-Unsubscribe": f"<{base}{UNSUBSCRIBE_PATH}?token={token}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
    return f"{base}/unsubscribe#token={token}", headers


def _signature(user_id: uuid.UUID, settings: Settings) -> str:
    message = f"{_PURPOSE}{user_id}".encode()
    digest = hmac.new(settings.secret_key.get_secret_value().encode(), message, hashlib.sha256)
    return base64.urlsafe_b64encode(digest.digest()).rstrip(b"=").decode()

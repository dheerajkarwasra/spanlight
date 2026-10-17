"""Offline license keys for the enterprise edition.

A key is `spl_lic_<payload>.<signature>`: both segments unpadded base64url, the payload a JSON
object and the signature Ed25519 over the payload segment's ASCII bytes exactly as received, so
no canonical JSON is needed. The public keys that verify it ship in `PUBLIC_KEYS`, keyed by
`kid`; the private key never enters the repository (`scripts/ee_license.py` mints keys from it).
Nothing calls home: each process verifies the key in its own environment.

A key that does not verify is an error, raised when the settings load (`config.py` calls
`parse_license`), because an operator who set one wants to know it is broken. Expiry is not:
an expired license loads, and every gate refuses its features, judged against the caller's
clock so a key that expires while the process runs turns them off without a restart.

This module imports `Settings` for typing only. `config.py` imports `parse_license` at runtime to
reject a bad key when the settings load, so the dependency must not point back.
"""

import base64
import binascii
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

if TYPE_CHECKING:
    from app.config import Settings

KEY_PREFIX = "spl_lic_"
FORMAT_VERSION = 1
# Far above any real key (a few hundred bytes), and checked before anything is decoded.
MAX_KEY_LENGTH = 4096

# The raw 32-byte Ed25519 public keys that sign licenses, by `kid`. Rotating means adding a key
# here and minting new licenses under it; a key is removed only once nothing signed by it is in
# use. There is deliberately no setting that adds a key: that would let anyone sign their own.
PUBLIC_KEYS: Mapping[str, bytes] = {
    "2026-10": bytes.fromhex("6ed1e9fa04d141cf4a287b3ca1a34fcb3667785aa9c2c2d4848f643ac88c0c6c"),
}

_BASE64URL = re.compile(r"[A-Za-z0-9_-]+")


class LicenseError(ValueError):
    """The key is malformed, of an unknown version, or not signed by a known key.

    A ValueError so pydantic reports it when the settings load. Messages never quote the key.
    """


@dataclass(frozen=True)
class License:
    """A verified license. `features` are names; ones this build does not know are ignored."""

    license_id: str
    kid: str
    licensee: str
    issued_at: datetime
    expires_at: datetime
    features: frozenset[str]

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at

    def allows(self, feature: str, now: datetime) -> bool:
        return not self.is_expired(now) and feature in self.features


def parse_license(key: str, public_keys: Mapping[str, bytes] | None = None) -> License:
    """Verify `key` and return what it grants. Raises `LicenseError`; never judges expiry.

    Only `v` and `kid` are read before the signature is checked, and only to pick the key.
    """
    keys = PUBLIC_KEYS if public_keys is None else public_keys
    key = key.strip()
    if len(key) > MAX_KEY_LENGTH:
        raise LicenseError("the key is too long to be a license key")
    if not key.startswith(KEY_PREFIX):
        raise LicenseError(f"the key does not start with {KEY_PREFIX}")
    payload_segment, separator, signature_segment = key.removeprefix(KEY_PREFIX).partition(".")
    if not separator:
        raise LicenseError("the key is not <payload>.<signature>")
    payload = _decode_json(_decode_segment(payload_segment, "payload"))
    signature = _decode_segment(signature_segment, "signature")

    if payload.get("v") != FORMAT_VERSION or type(payload.get("v")) is not int:
        raise LicenseError(f"the key's format version is not {FORMAT_VERSION}")
    kid = payload.get("kid")
    if not isinstance(kid, str) or kid not in keys:
        raise LicenseError("the key was not signed by a known signing key")
    try:
        Ed25519PublicKey.from_public_bytes(keys[kid]).verify(
            signature, payload_segment.encode("ascii")
        )
    except InvalidSignature:
        raise LicenseError("the key's signature does not match its contents") from None

    return License(
        license_id=_text(payload, "license_id"),
        kid=kid,
        licensee=_text(payload, "licensee"),
        issued_at=_instant(payload, "issued_at"),
        expires_at=_instant(payload, "expires_at"),
        features=_features(payload),
    )


def load_license(settings: "Settings") -> License | None:
    """The license in EE_LICENSE_KEY, or None (the community edition) when it is unset.

    The settings validator has already verified the key, so this cannot fail on settings that
    loaded.
    """
    if settings.ee_license_key is None:
        return None
    return parse_license(settings.ee_license_key.get_secret_value())


def _decode_segment(segment: str, name: str) -> bytes:
    # `b64decode(validate=True)` with altchars would still accept `+` and `/`, so the alphabet is
    # checked first; the padding the format leaves off is added back.
    if not _BASE64URL.fullmatch(segment):
        raise LicenseError(f"the key's {name} is not base64url")
    try:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (binascii.Error, ValueError):
        raise LicenseError(f"the key's {name} is not base64url") from None


def _decode_json(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise LicenseError("the key's payload is not JSON") from None
    if not isinstance(payload, dict):
        raise LicenseError("the key's payload is not a JSON object")
    return payload


def _text(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise LicenseError(f"the key's {field} is missing")
    return value


def _instant(payload: Mapping[str, Any], field: str) -> datetime:
    try:
        value = datetime.fromisoformat(_text(payload, field))
    except ValueError:
        raise LicenseError(f"the key's {field} is not an ISO 8601 time") from None
    if value.tzinfo is None:
        raise LicenseError(f"the key's {field} has no time zone")
    return value


def _features(payload: Mapping[str, Any]) -> frozenset[str]:
    value = payload.get("features")
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise LicenseError("the key's features are not a list of names")
    return frozenset(value)

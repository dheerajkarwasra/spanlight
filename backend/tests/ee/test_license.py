"""Enterprise license keys: a key verifies only when signed by a known key over its exact payload.

These are the checks that keep a forged or altered key from unlocking anything, so every way of
breaking a key is tried against a keypair generated here. The real public key is never involved.
"""

import base64
import json
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import Depends, FastAPI
from pydantic import ValidationError

from app.config import Settings
from app.core.errors import install_error_handlers
from app.ee import license as license_module
from app.ee.gate import license_allows, require_ee
from app.ee.license import License, LicenseError, parse_license

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


class _Feature(StrEnum):
    # EEFeature is empty until the first feature is built; the gate only reads `.value`.
    AUDIT_STREAMING = "audit_streaming"
    SCIM = "scim"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _raw_public(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "v": 1,
        "kid": "test",
        "license_id": "8d5f2c1e-0000-4000-8000-000000000001",
        "licensee": "Acme Inc",
        "issued_at": "2026-10-01T00:00:00+00:00",
        "expires_at": "2027-10-01T00:00:00+00:00",
        "features": ["audit_streaming"],
    }
    payload.update(overrides)
    return payload


def _mint(private_key: Ed25519PrivateKey, payload: dict[str, Any]) -> str:
    segment = _b64(json.dumps(payload).encode())
    return f"spl_lic_{segment}.{_b64(private_key.sign(segment.encode('ascii')))}"


@pytest.fixture
def signer() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
def keys(signer: Ed25519PrivateKey) -> dict[str, bytes]:
    return {"test": _raw_public(signer)}


def test_a_valid_key_is_read(signer: Ed25519PrivateKey, keys: dict[str, bytes]) -> None:
    parsed = parse_license(f"  {_mint(signer, _payload())}\n", keys)
    assert parsed == License(
        license_id="8d5f2c1e-0000-4000-8000-000000000001",
        kid="test",
        licensee="Acme Inc",
        issued_at=datetime(2026, 10, 1, tzinfo=UTC),
        expires_at=datetime(2027, 10, 1, tzinfo=UTC),
        features=frozenset({"audit_streaming"}),
    )
    assert parsed.allows("audit_streaming", NOW)
    assert not parsed.allows("scim", NOW)


def test_an_altered_payload_does_not_verify(
    signer: Ed25519PrivateKey, keys: dict[str, bytes]
) -> None:
    # A payload granting more, glued to the signature of the original one.
    signature = _mint(signer, _payload()).rsplit(".", 1)[1]
    widened = _b64(json.dumps(_payload(features=["audit_streaming", "scim"])).encode())
    with pytest.raises(LicenseError, match="signature does not match"):
        parse_license(f"spl_lic_{widened}.{signature}", keys)


def test_a_key_signed_by_another_private_key_does_not_verify(keys: dict[str, bytes]) -> None:
    forged = _mint(Ed25519PrivateKey.generate(), _payload())
    with pytest.raises(LicenseError, match="signature does not match"):
        parse_license(forged, keys)


def test_an_unknown_kid_is_refused(signer: Ed25519PrivateKey, keys: dict[str, bytes]) -> None:
    with pytest.raises(LicenseError, match="not signed by a known signing key"):
        parse_license(_mint(signer, _payload(kid="other")), keys)


@pytest.mark.parametrize("version", [2, 0, "1", True, None])
def test_only_format_version_1_is_read(
    signer: Ed25519PrivateKey, keys: dict[str, bytes], version: object
) -> None:
    with pytest.raises(LicenseError, match="format version"):
        parse_license(_mint(signer, _payload(v=version)), keys)


@pytest.mark.parametrize(
    "key",
    [
        "",
        "spl_live_abc.def",
        "spl_lic_nodot",
        "spl_lic_a+b.c",
        "spl_lic_" + _b64(b"not json") + ".AAAA",
        "spl_lic_" + _b64(b"[1, 2]") + ".AAAA",
        "spl_lic_" + "A" * 5000 + ".AAAA",
    ],
)
def test_malformed_keys_are_refused(keys: dict[str, bytes], key: str) -> None:
    with pytest.raises(LicenseError):
        parse_license(key, keys)


@pytest.mark.parametrize(
    "overrides",
    [
        {"licensee": ""},
        {"expires_at": "2027-10-01"},
        {"expires_at": "next year"},
        {"features": "audit_streaming"},
        {"features": [1]},
    ],
)
def test_signed_but_invalid_fields_are_refused(
    signer: Ed25519PrivateKey, keys: dict[str, bytes], overrides: dict[str, Any]
) -> None:
    with pytest.raises(LicenseError):
        parse_license(_mint(signer, _payload(**overrides)), keys)


def test_an_expired_license_loads_and_grants_nothing(
    signer: Ed25519PrivateKey, keys: dict[str, bytes]
) -> None:
    parsed = parse_license(_mint(signer, _payload(expires_at="2026-10-15T12:00:00+00:00")), keys)
    assert parsed.is_expired(NOW)
    assert not parsed.allows("audit_streaming", NOW)
    assert parsed.allows("audit_streaming", NOW - timedelta(seconds=1))
    assert not license_allows(None, _Feature.AUDIT_STREAMING, NOW)  # type: ignore[arg-type]


def test_settings_refuse_a_key_that_does_not_verify_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, signer: Ed25519PrivateKey, keys: dict[str, bytes]
) -> None:
    monkeypatch.setattr(license_module, "PUBLIC_KEYS", keys)
    good = _mint(signer, _payload())
    assert Settings(_env_file=None, ee_license_key=good).ee_license_key is not None
    assert Settings(_env_file=None, ee_license_key="").ee_license_key is None

    forged = _mint(Ed25519PrivateKey.generate(), _payload())
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, ee_license_key=forged)
    assert "EE_LICENSE_KEY is invalid" in str(caught.value)
    assert forged.split(".")[1] not in str(caught.value)
    assert forged.split(".")[1] not in json.dumps(
        caught.value.errors(include_url=False), default=str
    )


async def test_an_unlicensed_feature_answers_403_with_its_name(
    signer: Ed25519PrivateKey, keys: dict[str, bytes]
) -> None:
    application = FastAPI()
    install_error_handlers(application)
    application.state.clock = lambda: NOW
    application.state.ee_license = parse_license(_mint(signer, _payload()), keys)

    for feature in _Feature:

        @application.get(f"/{feature.value}", dependencies=[Depends(require_ee(feature))])  # type: ignore[arg-type]
        async def route() -> dict[str, str]:
            return {"ok": "yes"}

    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        assert (await http.get("/audit_streaming")).json() == {"ok": "yes"}
        refused = await http.get("/scim")
        application.state.ee_license = None
        unlicensed = await http.get("/audit_streaming")

    for response, feature in ((refused, "scim"), (unlicensed, "audit_streaming")):
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("application/problem+json")
        body = response.json()
        assert (body["code"], body["feature"]) == ("EE_LICENSE_REQUIRED", feature)

"""`GET /auth/me` says what this server can do, as booleans and the gateway mode, never secrets."""

import base64
import secrets
from typing import Any

from pydantic import SecretStr

from app.config import Settings
from tests.conftest import BrowserFactory, _browsers, _running


async def test_me_capabilities_on_a_server_with_nothing_configured(
    browser_factory: BrowserFactory,
) -> None:
    browser = await browser_factory("ada@example.com")

    me = await browser.get("/api/v1/auth/me")

    assert me.status_code == 200
    assert me.json()["capabilities"] == {
        "gateway_mode": "embedded",
        "credentials_configured": False,
        "exports_configured": False,
    }


async def test_me_capabilities_when_keys_and_object_storage_are_set(settings: Settings) -> None:
    key = base64.b64encode(secrets.token_bytes(32)).decode()
    configured = settings.model_copy(
        update={
            "gateway_mode": "disabled",
            "credentials_keys": SecretStr(f"capkey1:{key}"),
            "s3_bucket": "cap-bucket",
            "s3_access_key": SecretStr("cap-access-key"),
            "s3_secret_key": SecretStr("cap-secret-key"),
        }
    )

    async with _running(configured) as app, _browsers(app) as factory:
        browser = await factory("ada@example.com")
        me = await browser.get("/api/v1/auth/me")

    assert me.status_code == 200
    body: dict[str, Any] = me.json()
    assert body["capabilities"] == {
        "gateway_mode": "disabled",
        "credentials_configured": True,
        "exports_configured": True,
    }
    for secret in (key, "capkey1", "cap-bucket", "cap-access-key", "cap-secret-key"):
        assert secret not in me.text

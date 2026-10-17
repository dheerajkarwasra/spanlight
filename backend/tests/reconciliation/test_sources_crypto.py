"""A billing source's admin key at rest: sealed under CREDENTIALS_KEYS, never in the clear.

Failure modes: the key is stored as sent, or under no key id, so a database dump or a backup
hands it over; a server with another keyring opens it.
"""

import base64
from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.crypto import Sealed, UnknownKeyId, decrypt
from app.db.models.billing import BillingSource
from tests.helpers import Browser, create_workspace
from tests.reconciliation.fetch_support import ANTHROPIC_KEY, OPENAI_KEY
from tests.reconciliation.sources_support import (
    ProviderApi,
    answering,
    create_source,
    running_app,
    secret_windows,
)

OTHER_KEYRING = "elsewhere:" + base64.b64encode(bytes(range(64, 96))).decode()


@pytest.fixture
def provider_api() -> ProviderApi:
    return answering()


@pytest.fixture
async def app(gateway_settings: Settings, provider_api: ProviderApi) -> AsyncIterator[Any]:
    async with running_app(gateway_settings, provider_api) as application:
        yield application


@pytest.fixture
async def sources(browser_factory: Any) -> dict[str, str]:
    owner: Browser = await browser_factory("owner@example.com")
    workspace = await create_workspace(owner)
    anthropic = await create_source(owner, workspace.org_id)
    openai = await create_source(owner, workspace.org_id, kind="openai_costs_api", name="OpenAI")
    return {"anthropic": anthropic["id"], "openai": openai["id"]}


async def stored(
    session_factory: async_sessionmaker[AsyncSession], source_id: str
) -> BillingSource:
    async with session_factory() as db:
        source = await db.scalar(select(BillingSource).where(BillingSource.id == source_id))
    assert source is not None
    return source


async def test_sealed_bytes_differ_from_the_key(
    sources: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
    gateway_settings: Settings,
) -> None:
    for name, key in (("anthropic", ANTHROPIC_KEY), ("openai", OPENAI_KEY)):
        source = await stored(session_factory, sources[name])
        assert source.ciphertext is not None
        assert source.key_id == "gateway-test"
        assert source.ciphertext != key.encode()
        assert key.encode() not in source.ciphertext
        sealed = Sealed(ciphertext=source.ciphertext, key_id=source.key_id)
        assert decrypt(sealed, settings=gateway_settings).decode() == key


async def test_another_keyring_cannot_open_the_key(
    sources: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
    gateway_settings: Settings,
) -> None:
    source = await stored(session_factory, sources["anthropic"])
    assert source.ciphertext is not None and source.key_id is not None
    elsewhere = gateway_settings.model_copy(update={"credentials_keys": SecretStr(OTHER_KEYRING)})

    with pytest.raises(UnknownKeyId):
        decrypt(Sealed(ciphertext=source.ciphertext, key_id=source.key_id), settings=elsewhere)


async def test_a_dump_of_billing_sources_holds_only_ciphertext(
    sources: dict[str, str], session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as db:
        rows = list(await db.scalars(text("SELECT b::text FROM billing_sources b")))
        sealed = list(await db.scalars(text("SELECT ciphertext FROM billing_sources")))

    assert len(rows) == len(sealed) == 2
    dump = "".join(rows) + "".join(bytes(value).decode("latin-1") for value in sealed)
    for key in (ANTHROPIC_KEY, OPENAI_KEY):
        assert not [window for window in secret_windows(key) if window in dump]


def test_repr_shows_ids_only() -> None:
    source = BillingSource(
        org_id="00000000-0000-7000-8000-000000000001",
        name="Prod account",
        ciphertext=b"sealed",
        key_id="v1",
    )
    assert "sealed" not in repr(source)
    assert "Prod account" not in repr(source)

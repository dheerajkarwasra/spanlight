"""A gateway key's context is reused per process, like an API key's row (`app.api.key_cache`).

A hit skips the key lookup and the context query, never the secret check. A key revoked or
edited on this process is refused or re-read from the next call; one revoked through another
process stops working within the cache's TTL, as API keys do.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import key_cache
from app.db.rls import bypass_rls
from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import FakeProvider, fixture_text

CHAT_BODY = {"model": "gpt-4.1-mini", "messages": [{"role": "user", "content": "Hello"}]}
CHAT_PATH = "/gw/v1/chat/completions"


async def _chat(workspace: GatewayWorkspace, fake_provider: FakeProvider | None = None) -> int:
    """One chat call's status; the provider is scripted only for a call expected to reach it."""
    if fake_provider is not None:
        fake_provider.reply_json(fixture_text("openai/chat.json").encode())
    response = await workspace.gw.post(CHAT_PATH, json=CHAT_BODY)
    return response.status_code


async def test_a_second_call_skips_the_key_lookup(
    gateway_app: Any, gateway_workspace: GatewayWorkspace, fake_provider: FakeProvider
) -> None:
    assert await _chat(gateway_workspace, fake_provider) == 200
    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_rest: Any) -> None:
        statements.append(statement)

    engine = gateway_app.state.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert await _chat(gateway_workspace, fake_provider) == 200
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert not [s for s in statements if "gateway_key_prefix" in s or "FROM gateway_keys" in s]
    assert not [s for s in statements if "FROM projects JOIN gateway_routes" in s]


async def test_a_wrong_secret_is_refused_on_a_cached_key(
    gateway_app: Any, gateway_workspace: GatewayWorkspace, fake_provider: FakeProvider
) -> None:
    assert await _chat(gateway_workspace, fake_provider) == 200
    forged = gateway_workspace.gateway_key[:-4] + (
        "aaaa" if not gateway_workspace.gateway_key.endswith("aaaa") else "bbbb"
    )

    response = await gateway_workspace.gw.post(
        CHAT_PATH, json=CHAT_BODY, headers={"Authorization": f"Bearer {forged}"}
    )

    assert response.status_code == 401


async def test_a_key_revoked_here_is_refused_on_the_next_call(
    gateway_workspace: GatewayWorkspace, fake_provider: FakeProvider
) -> None:
    assert await _chat(gateway_workspace, fake_provider) == 200
    revoked = await gateway_workspace.owner.delete(
        f"/api/v1/projects/{gateway_workspace.project_id}/gateway/keys/"
        f"{gateway_workspace.gateway_key_id}"
    )
    assert revoked.status_code == 204, revoked.text

    assert await _chat(gateway_workspace) == 401


async def test_a_key_edited_here_is_read_again_on_the_next_call(
    gateway_workspace: GatewayWorkspace, fake_provider: FakeProvider
) -> None:
    assert await _chat(gateway_workspace, fake_provider) == 200
    edited = await gateway_workspace.owner.patch(
        f"/api/v1/projects/{gateway_workspace.project_id}/gateway/keys/"
        f"{gateway_workspace.gateway_key_id}",
        json={"allowed_models": ["gpt-4o"]},
    )
    assert edited.status_code == 200, edited.text

    assert await _chat(gateway_workspace) == 404


async def test_a_key_revoked_elsewhere_is_refused_after_the_ttl(
    gateway_workspace: GatewayWorkspace,
    gateway_settings: Any,
    fake_provider: FakeProvider,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [1_000.0]
    monkeypatch.setattr(key_cache, "time", SimpleNamespace(monotonic=lambda: now[0]))
    assert await _chat(gateway_workspace, fake_provider) == 200
    async with session_factory() as db:  # another api process revokes the key
        await bypass_rls(db)
        await db.execute(
            text("UPDATE gateway_keys SET revoked_at = now() WHERE id = :id"),
            {"id": gateway_workspace.gateway_key_id},
        )
        await db.commit()

    now[0] += gateway_settings.api_key_cache_ttl_seconds

    assert await _chat(gateway_workspace) == 401

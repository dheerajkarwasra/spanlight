"""Real provider closure and accounting survive admitted request failure."""

import asyncio
import json
import uuid
from contextlib import AsyncExitStack
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db.rls import bypass_rls
from tests.asgi_harness import ClientGone, call_asgi
from tests.gateway.admission_support import CHAT, created, unfinished_request, wait_active, world
from tests.gateway.support import key_minute_counts
from tests.gateway_support import drain_recorder
from tests.half_sent_body import response_status, send_half_and_leave


@pytest.mark.parametrize("standalone", [False, True])
@pytest.mark.parametrize("failure", ["disconnect", "cancel", "send", "upstream"])
async def test_failure_recovers_capacity_and_preserves_unknown_accounting(
    gateway_settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    standalone: bool,
    failure: str,
) -> None:
    async with AsyncExitStack() as stack:
        w = await world(stack, gateway_settings, standalone, cap=1)
        await created(
            w.owner,
            f"/api/v1/orgs/{w.workspace.org_id}/price-overrides",
            {
                "provider": "openai_compatible",
                "model_pattern": "gpt-4.1-mini",
                "input_per_mtok": "1",
                "output_per_mtok": "1",
                "cached_input_per_mtok": "1",
            },
        )
        prefix = f"/api/v1/projects/{w.workspace.project_id}"
        limited = await w.owner.patch(prefix + f"/gateway/keys/{w.key_id}", json={"rpm_limit": 2})
        assert limited.status_code == 200
        budget = await created(
            w.owner,
            prefix + "/budgets",
            {
                "name": "local",
                "scope": "project",
                "period": "daily",
                "amount_usd": "1",
                "action": "block",
                "channel_ids": [],
            },
        )
        headers = [("Authorization", f"Bearer {w.key}"), ("Content-Type", "application/json")]
        reached = asyncio.Event()
        fail_send = asyncio.Event()

        async def sending(message: Any) -> None:
            if message["type"] == "http.response.body" and message.get("body"):
                reached.set()
                if failure == "send":
                    await fail_send.wait()
                    raise ClientGone("closed local socket")

        task = None
        if failure in {"cancel", "send"}:
            task = asyncio.create_task(
                call_asgi(
                    w.application,
                    "/gw/v1/chat/completions",
                    headers=headers,
                    body=json.dumps(CHAT).encode(),
                    on_message=sending,
                    spec_version="2.3",
                )
            )
            await asyncio.wait_for(reached.wait(), 5)
        else:
            response = await stack.enter_async_context(
                w.client.stream("POST", "/gw/v1/chat/completions", json=CHAT)
            )
            assert response.status_code == 200
            iterator = response.aiter_bytes()
            await anext(iterator)
        try:
            await wait_active(w.provider, 1)
            status, _, _ = await unfinished_request(w.base, "/gw/v1/chat/completions", w.key)
            assert status == 503
            async with session_factory() as db:
                await bypass_rls(db)
                states = list(
                    await db.scalars(
                        text("SELECT status FROM budget_reservations WHERE budget_id = :id"),
                        {"id": budget["id"]},
                    )
                )
                held_amount = await db.scalar(
                    text("SELECT amount_usd FROM budget_reservations WHERE budget_id = :id"),
                    {"id": budget["id"]},
                )
            assert states == ["held"]
            assert held_amount > 0
            assert await key_minute_counts(session_factory, uuid.UUID(w.key_id)) == (0, 0)
            if failure == "cancel":
                assert task is not None
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            elif failure == "send":
                assert task is not None
                fail_send.set()
                with pytest.raises(ClientGone):
                    await task
            elif failure == "upstream":
                w.provider.broken = True
                w.provider.release.set()
                async for _ in iterator:
                    pass
            else:
                await response.aclose()
            await wait_active(w.provider, 0)
            await drain_recorder()
            async with session_factory() as db:
                await bypass_rls(db)
                states = list(
                    await db.scalars(
                        text("SELECT status FROM budget_reservations WHERE budget_id = :id"),
                        {"id": budget["id"]},
                    )
                )
                charged = await db.scalar(
                    text("SELECT charged_usd FROM budget_reservations WHERE budget_id = :id"),
                    {"id": budget["id"]},
                )
                settled, reserved = (
                    await db.execute(
                        text(
                            "SELECT settled_usd, reserved_usd FROM budget_counters "
                            "WHERE budget_id = :id"
                        ),
                        {"id": budget["id"]},
                    )
                ).one()
            assert states == ["settled"]
            assert charged == held_amount
            assert (settled, reserved) == (held_amount, 0)
            assert await key_minute_counts(session_factory, uuid.UUID(w.key_id)) == (1, 0)
            history = await w.owner.get(prefix + "/traces")
            [trace] = history.json()["items"]
            assert trace["cost_usd"] is None
            detail = await w.owner.get(prefix + "/traces/" + trace["trace_id"])
            [span] = detail.json()["spans"]
            assert span["status"] == "error"
            assert span["cost_usd"] is None
            assert w.provider.calls == 1
            assert (await w.client.get("/gw/v1/models")).status_code == 200
        finally:
            fail_send.set()
            w.provider.release.set()
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("standalone", [False, True])
async def test_partial_upload_and_validation_errors_return_capacity(
    gateway_settings: Settings,
    standalone: bool,
) -> None:
    async with AsyncExitStack() as stack:
        w = await world(stack, gateway_settings, standalone, cap=1)
        sent = await send_half_and_leave(
            w.application,
            "/gw/v1/chat/completions",
            [("Authorization", f"Bearer {w.key}"), ("Content-Type", "application/json")],
            b'{"model":"gpt-4.1-mini",',
        )
        assert response_status(sent) == 400
        for _ in range(3):
            assert (await w.client.post("/gw/v1/chat/completions", json={})).status_code == 400
            assert (
                await w.client.get("/gw/v1/models", headers={"Authorization": ""})
            ).status_code == 401
        assert w.provider.calls == 0
        assert (await w.client.get("/gw/v1/models")).status_code == 200

"""Pre-auth token-bucket flood shedding in GatewayAdmissionMiddleware."""

import asyncio
import json
from typing import Any

from app.gateway.admission import GatewayAdmissionMiddleware


def _scope(
    path: str = "/gw/v1/chat/completions", auth: str = "", client_ip: str = "10.0.0.1"
) -> dict[str, Any]:
    headers: list[tuple[bytes, bytes]] = [(b"content-length", b"32")]
    if auth:
        headers.append((b"authorization", f"Bearer {auth}".encode("latin-1")))
    return {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": headers,
        "client": (client_ip, 51234),
    }


async def _receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"{}", "more_body": False}


def test_preauth_sheds_flood_with_429_and_refills_over_time() -> None:
    now = 100.0
    passed = 0

    async def app(scope: Any, receive: Any, send: Any) -> None:
        nonlocal passed
        passed += 1
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}", "more_body": False})

    middleware = GatewayAdmissionMiddleware(
        app,
        limit=50,
        limit_bytes=10_000_000,
        limit_per_key=50,
        rate_per_second=2.0,
        burst=2,
        clock=lambda: now,
        max_buckets=4,
    )

    async def invoke(scope: dict[str, Any]) -> tuple[int, dict[str, str], dict[str, Any]]:
        status = 0
        resp_headers: dict[str, str] = {}
        body = bytearray()

        async def send(message: dict[str, Any]) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                resp_headers.update(
                    {k.decode("latin-1"): v.decode("latin-1") for k, v in message["headers"]}
                )
            elif message["type"] == "http.response.body":
                body.extend(message.get("body", b""))

        await middleware(scope, _receive, send)
        return status, resp_headers, json.loads(body or b"{}")

    async def run() -> None:
        nonlocal now
        s1, _, _ = await invoke(_scope(auth="sk-sl-key1"))
        s2, _, _ = await invoke(_scope(auth="sk-sl-key1"))
        s3, h3, b3 = await invoke(_scope(auth="sk-sl-key1"))
        assert (s1, s2, s3) == (200, 200, 429)
        assert float(h3["retry-after"]) >= 0.05
        assert b3["error"]["code"] == "rate_limit_exceeded"

        # Different IP/key keeps its own isolated bucket.
        s_other, _, _ = await invoke(_scope(auth="", client_ip="10.0.0.99"))
        assert s_other == 200

        # Advancing clock refills key1's bucket.
        now += 1.0
        s4, _, _ = await invoke(_scope(auth="sk-sl-key1"))
        assert s4 == 200

        # Bounded bucket eviction under randomized keys stays within max_buckets.
        for idx in range(10):
            await invoke(_scope(auth=f"sk-sl-flood-{idx}"))
        assert len(middleware._limiter._buckets) <= 4
        assert passed >= 10

    asyncio.run(run())

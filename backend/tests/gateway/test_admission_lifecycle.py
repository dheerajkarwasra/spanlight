"""The ASGI call owns the slot through finalization, including failed transports."""

import asyncio
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from prometheus_client import REGISTRY
from starlette.datastructures import Headers

from app.api.gw import MAX_REQUEST_BYTES
from app.config import Settings
from app.gateway.admission import GatewayAdmissionMiddleware, reserved_bytes

COUNT_REJECTED = ("spanlight_gateway_admission_rejections_total", {"reason": "count"})


class HeldApplication:
    """ASGI peer with an explicitly held finalizer, independent of middleware state."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.finish = asyncio.Event()
        self.cleaning = asyncio.Event()
        self.cleaned = asyncio.Event()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            self.entered.set()
            await self.finish.wait()
            await send({"type": "http.response.body", "body": b"done"})
        finally:
            self.cleaning.set()
            await self.cleaned.wait()


async def assert_refused(admission: Any, peer: HeldApplication, scope: Any) -> None:
    messages: list[Any] = []
    arrived = asyncio.Event()

    async def send(message: Any) -> None:
        messages.append(message)
        if message["type"] == "http.response.start":
            arrived.set()

    async def receive() -> Any:
        raise AssertionError("overload must not consume receive")

    task = asyncio.create_task(admission(scope, receive, send))
    try:
        await asyncio.wait_for(arrived.wait(), 1)
        assert messages[0]["status"] == 503
        await asyncio.wait_for(task, 1)
    finally:
        if not task.done():
            peer.finish.set()
            peer.cleaned.set()
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("failure", ["complete", "cancel", "send"])
async def test_capacity_waits_for_finalization_after_headers_or_failure(failure: str) -> None:
    before_rejected = REGISTRY.get_sample_value(*COUNT_REJECTED) or 0
    before_duration = (
        REGISTRY.get_sample_value("spanlight_gateway_admission_duration_seconds_count") or 0
    )
    peer = HeldApplication()
    admission = GatewayAdmissionMiddleware(
        peer, limit=1, limit_bytes=MAX_REQUEST_BYTES, limit_per_key=1
    )
    bodies: list[bytes] = []

    async def send(message: Any) -> None:
        if message["type"] == "http.response.body":
            if failure == "send":
                raise OSError("closed socket")
            bodies.append(message.get("body", b""))

    async def receive() -> Any:
        raise AssertionError("admission must not receive")

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/gw/v1/chat/completions",
        "headers": [],
        "query_string": b"",
        "server": ("localhost", 80),
    }
    task = asyncio.create_task(admission(scope, receive, send))
    try:
        await asyncio.wait_for(peer.entered.wait(), 1)
        messages: list[Any] = []

        async def collect(message: Any) -> None:
            messages.append(message)

        await assert_refused(admission, peer, scope)
        assert REGISTRY.get_sample_value("spanlight_gateway_in_flight") == 1
        if failure == "cancel":
            task.cancel()
        else:
            peer.finish.set()
        await asyncio.wait_for(peer.cleaning.wait(), 1)
        messages.clear()
        await assert_refused(admission, peer, scope)
        assert REGISTRY.get_sample_value("spanlight_gateway_in_flight") == 1
        assert (
            REGISTRY.get_sample_value("spanlight_gateway_admission_duration_seconds_count") or 0
        ) == before_duration
        peer.cleaned.set()
        if failure == "cancel":
            with pytest.raises(asyncio.CancelledError):
                await task
        elif failure == "send":
            with pytest.raises(OSError, match="closed socket"):
                await task
        else:
            await task
        messages.clear()
        peer.finish.set()
        async with asyncio.timeout(1):
            await admission(scope, receive, collect)
        assert messages[0]["status"] == 200
        assert REGISTRY.get_sample_value("spanlight_gateway_in_flight") == 0
        assert REGISTRY.get_sample_value(*COUNT_REJECTED) == before_rejected + 2
        assert (
            REGISTRY.get_sample_value("spanlight_gateway_admission_duration_seconds_count")
            == before_duration + 2
        )
    finally:
        peer.finish.set()
        peer.cleaned.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@given(
    cap=st.integers(min_value=1, max_value=4),
    actions=st.lists(st.sampled_from(["open", "close", "cancel"]), min_size=1, max_size=20),
)
@settings(max_examples=20, deadline=None, derandomize=True)
async def test_bounded_slot_sequences_recover(cap: int, actions: list[str]) -> None:
    finish: asyncio.Queue[asyncio.Event] = asyncio.Queue()

    async def peer(scope: Any, receive: Any, send: Any) -> None:
        event = asyncio.Event()
        await finish.put(event)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await event.wait()
        await send({"type": "http.response.body", "body": b"ok"})

    admission = GatewayAdmissionMiddleware(
        peer, limit=cap, limit_bytes=cap * MAX_REQUEST_BYTES, limit_per_key=cap
    )
    active: list[tuple[asyncio.Task[Any], asyncio.Event]] = []

    async def started_request() -> tuple[asyncio.Task[Any], list[int]]:
        headers_seen = asyncio.Event()
        statuses: list[int] = []

        async def send(message: Any) -> None:
            if message["type"] == "http.response.start":
                statuses.append(message["status"])
                headers_seen.set()

        async def receive() -> Any:
            raise AssertionError("body unexpectedly consumed")

        task = asyncio.create_task(
            admission(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/gw/v1/models",
                    "headers": [],
                    "query_string": b"",
                    "server": ("localhost", 80),
                },
                receive,
                send,
            )
        )
        try:
            await asyncio.wait_for(headers_seen.wait(), 1)
        except BaseException:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        return task, statuses

    try:
        for action in actions:
            if action == "open":
                task, statuses = await started_request()
                if len(active) == cap:
                    assert statuses == [503]
                    await task
                else:
                    assert statuses == [200]
                    active.append((task, await finish.get()))
            elif active:
                task, event = active.pop(0)
                if action == "cancel":
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    event.set()
                    await task
        for task, event in active:
            event.set()
            await task
    finally:
        for task, event in active:
            event.set()
            task.cancel()
        await asyncio.gather(*(task for task, _ in active), return_exceptions=True)


def gateway_scope(
    *headers: tuple[bytes, bytes], method: str = "POST", path: str = "/gw/v1/chat/completions"
) -> dict[str, Any]:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": list(headers),
        "query_string": b"",
        "server": ("localhost", 80),
    }


def rejected(reason: str) -> float:
    return (
        REGISTRY.get_sample_value(
            "spanlight_gateway_admission_rejections_total", {"reason": reason}
        )
        or 0
    )


class Streams:
    """Admitted calls held open until `close`, each ending on an ASGI disconnect."""

    def __init__(self, admission_factory: Any) -> None:
        self.closed = asyncio.Event()
        self.admission = admission_factory(self.peer)
        self.tasks: list[asyncio.Task[Any]] = []

    async def peer(self, scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        assert (await receive())["type"] == "http.disconnect"

    async def open(self, *headers: tuple[bytes, bytes], **target: str) -> int:
        started = asyncio.Event()
        statuses: list[int] = []

        async def send(message: Any) -> None:
            if message["type"] == "http.response.start":
                statuses.append(message["status"])
                started.set()

        async def receive() -> Any:
            await self.closed.wait()
            return {"type": "http.disconnect"}

        self.tasks.append(
            asyncio.create_task(self.admission(gateway_scope(*headers, **target), receive, send))
        )
        await asyncio.wait_for(started.wait(), 1)
        return statuses[0]

    async def close(self) -> None:
        self.closed.set()
        await asyncio.wait_for(asyncio.gather(*self.tasks), 5)


def defaults(peer: Any) -> GatewayAdmissionMiddleware:
    settings = Settings(_env_file=None)
    return GatewayAdmissionMiddleware(
        peer,
        limit=settings.gateway_max_in_flight,
        limit_bytes=settings.gateway_max_in_flight_bytes,
        limit_per_key=settings.gateway_max_in_flight_per_key,
    )


async def test_small_streams_admit_beyond_eight() -> None:
    streams = Streams(defaults)
    try:
        statuses = [
            await streams.open(
                (b"content-length", b"256"), (b"authorization", f"k{i % 2}".encode())
            )
            for i in range(64)
        ]
        assert statuses == [200] * 64
        assert streams.admission.in_flight == 64
        assert streams.admission.in_flight_bytes == 64 * 256
    finally:
        await streams.close()
    assert streams.admission.in_flight == streams.admission.in_flight_bytes == 0


async def test_byte_budget_rejects_ninth_large_body() -> None:
    before = rejected("bytes")
    streams = Streams(
        lambda peer: GatewayAdmissionMiddleware(
            peer, limit=256, limit_bytes=8 * MAX_REQUEST_BYTES, limit_per_key=128
        )
    )
    large = (b"content-length", str(MAX_REQUEST_BYTES).encode())
    try:
        assert [await streams.open(large) for _ in range(8)] == [200] * 8
        assert await streams.open(large) == 503
        assert rejected("bytes") == before + 1
        assert streams.admission.in_flight == 8
    finally:
        await streams.close()


async def test_chunked_request_reserves_full_cap_and_releases() -> None:
    streams = Streams(defaults)
    await streams.open((b"transfer-encoding", b"chunked"))
    assert streams.admission.in_flight_bytes == MAX_REQUEST_BYTES
    await streams.close()
    assert streams.admission.in_flight_bytes == 0


async def test_per_key_share() -> None:
    before = rejected("key")
    streams = Streams(
        lambda peer: GatewayAdmissionMiddleware(
            peer, limit=256, limit_bytes=83_886_080, limit_per_key=2
        )
    )
    key_a = (b"authorization", b"Bearer spl_gw_a")
    try:
        assert [await streams.open(key_a) for _ in range(2)] == [200, 200]
        assert await streams.open(key_a) == 503
        assert rejected("key") == before + 1
        assert await streams.open((b"x-api-key", b"spl_gw_b")) == 200
        assert len(streams.admission.per_key) == 2
    finally:
        await streams.close()
    assert streams.admission.per_key == {}


async def test_key_entries_removed_at_zero() -> None:
    async def peer(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    admission = defaults(peer)

    async def ignore(message: Any) -> None:
        pass

    async def receive() -> Any:
        raise AssertionError("body unexpectedly consumed")

    for i in range(10_000):
        scope = gateway_scope((b"content-length", b"0"), (b"authorization", f"k{i}".encode()))
        await admission(scope, receive, ignore)
    assert len(admission.per_key) == 0


async def test_bodyless_requests_reserve_no_bytes() -> None:
    streams = Streams(
        lambda peer: GatewayAdmissionMiddleware(
            peer, limit=512, limit_bytes=MAX_REQUEST_BYTES, limit_per_key=256
        )
    )
    try:
        for method in ("GET", "GET", "HEAD", "OPTIONS"):
            assert await streams.open(method=method, path="/gw/v1/models") == 200
        assert await streams.open((b"content-length", b"0")) == 200
        assert streams.admission.in_flight_bytes == 0
        assert await streams.open((b"transfer-encoding", b"chunked")) == 200
        assert streams.admission.in_flight_bytes == MAX_REQUEST_BYTES
    finally:
        await streams.close()


@pytest.mark.parametrize(
    "variant",
    [
        (b"authorization", b"Bearer spl_gw_k"),
        (b"authorization", b"bearer spl_gw_k"),
        (b"authorization", b"BEARER spl_gw_k"),
        (b"authorization", b"Bearer  spl_gw_k "),
        (b"x-api-key", b" spl_gw_k"),
    ],
)
async def test_key_share_uses_the_credential_auth_reads(variant: tuple[bytes, bytes]) -> None:
    before = rejected("key")
    streams = Streams(
        lambda peer: GatewayAdmissionMiddleware(
            peer, limit=512, limit_bytes=83_886_080, limit_per_key=1
        )
    )
    try:
        assert await streams.open((b"authorization", b"Bearer spl_gw_k")) == 200
        assert await streams.open(variant) == 503
        assert rejected("key") == before + 1
        assert await streams.open((b"x-api-key", b"spl_gw_other")) == 200
    finally:
        await streams.close()


async def test_odd_credentials_still_get_a_bucket() -> None:
    streams = Streams(
        lambda peer: GatewayAdmissionMiddleware(
            peer, limit=512, limit_bytes=83_886_080, limit_per_key=1
        )
    )
    conflicting = ((b"authorization", b"Bearer a"), (b"x-api-key", b"b"))
    try:
        assert await streams.open(*conflicting) == 200
        assert await streams.open(*conflicting) == 503
        assert await streams.open() == 200
        assert await streams.open((b"authorization", b"  ")) == 503
    finally:
        await streams.close()


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ([(b"content-length", b"0")], 0),
        ([(b"content-length", b"256")], 256),
        ([(b"content-length", str(MAX_REQUEST_BYTES).encode())], MAX_REQUEST_BYTES),
        ([(b"content-length", str(MAX_REQUEST_BYTES + 1).encode())], MAX_REQUEST_BYTES),
        ([(b"content-length", b"-1")], MAX_REQUEST_BYTES),
        ([(b"content-length", b"abc")], MAX_REQUEST_BYTES),
        ([(b"content-length", b"10, 10")], MAX_REQUEST_BYTES),
        ([(b"content-length", "\u00b2".encode("latin-1"))], MAX_REQUEST_BYTES),
        ([(b"content-length", b"")], MAX_REQUEST_BYTES),
        ([(b"content-length", b"9" * 5000)], MAX_REQUEST_BYTES),  # past int()'s digit limit
        ([], MAX_REQUEST_BYTES),
        ([(b"transfer-encoding", b"chunked"), (b"content-length", b"5")], MAX_REQUEST_BYTES),
        ([(b"content-length", b"5"), (b"transfer-encoding", b"gzip, chunked")], MAX_REQUEST_BYTES),
    ],
)
def test_reserved_bytes_on_a_post(headers: list[tuple[bytes, bytes]], expected: int) -> None:
    assert reserved_bytes("POST", Headers(raw=headers)) == expected


def test_rejection_reasons_start_at_zero() -> None:
    for reason in ("count", "bytes", "key"):
        assert rejected_sample(reason) is not None


def rejected_sample(reason: str) -> float | None:
    return REGISTRY.get_sample_value(
        "spanlight_gateway_admission_rejections_total", {"reason": reason}
    )

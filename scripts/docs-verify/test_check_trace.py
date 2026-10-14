"""check_trace.py must not return a stale or partial trace, and must not hide API errors."""

# ruff: noqa: S101 (asserts are what a test is made of)

from collections.abc import Callable
from datetime import UTC, datetime

import check_trace
import httpx
import pytest

SINCE = datetime(2026, 1, 1, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def trace(spans: int) -> dict:
    return {"trace_id": "t1", "spans": [{}] * spans}


def client_for(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(base_url="http://api.invalid", transport=httpx.MockTransport(handler))


def run(handler: Callable[[httpx.Request], httpx.Response], wait: float = 5) -> dict | None:
    clock = Clock()
    return check_trace.wait_for_trace(
        client_for(handler), "p1", None, SINCE, wait, sleep=clock.sleep, clock=clock
    )


def test_waits_until_the_span_count_stops_growing() -> None:
    counts = iter([1, 2, 3, 3, 3])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/traces"):
            return httpx.Response(200, json={"items": [{"trace_id": "t1"}]})
        return httpx.Response(200, json=trace(next(counts)))

    result = run(handler)
    assert result is not None
    assert len(result["spans"]) == 3


def test_gives_up_when_nothing_arrives() -> None:
    assert run(lambda request: httpx.Response(200, json={"items": []})) is None


def test_retries_a_missing_trace_by_id_but_not_an_auth_error() -> None:
    clock = Clock()
    client = client_for(lambda request: httpx.Response(404, json={}))
    assert (
        check_trace.wait_for_trace(client, "p1", "t1", SINCE, 3, sleep=clock.sleep, clock=clock)
        is None
    )
    assert clock.now >= 3  # it waited out the deadline

    for status in (401, 403, 500):
        with pytest.raises(check_trace.CheckError, match=str(status)):
            run(lambda request, status=status: httpx.Response(status, json={}))
        with pytest.raises(check_trace.CheckError, match=str(status)):
            check_trace.wait_for_trace(
                client_for(lambda request, status=status: httpx.Response(status, json={})),
                "p1",
                "t1",
                SINCE,
                3,
                sleep=Clock().sleep,
                clock=Clock(),
            )

"""Idempotency keys: a retried POST runs once and gets the first answer back.

The tests run against a copy of the real app with probe routes added, because no production route
uses `idempotent()` yet. The probe handler counts how often it ran, which is the whole point of
the feature, and can be told (through the request body) to wait, to fail, or to answer oddly.
"""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any

import httpx
import pytest
from fastapi import Body, Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from prometheus_client import REGISTRY
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from starlette.background import BackgroundTask
from starlette.types import Receive, Scope, Send

from app.api.deps import Access, CurrentPrincipal, current_principal, require
from app.api.principals import ApiKeyPrincipal, Principal, SessionPrincipal, TokenPrincipal
from app.config import Settings
from app.core.errors import ProblemError, conflict, forbidden
from app.core.idempotency import (
    MAX_KEPT_BODY_BYTES,
    idempotent,
    principal_key,
    request_fingerprint,
)
from app.core.permissions import Permission
from app.db.models import Session, TokenScope, User
from app.main import create_app
from tests.conftest import TEST_ORIGIN, BrowserFactory
from tests.helpers import Browser, bearer, create_token, create_workspace, join_with_role

SessionFactory = async_sessionmaker[AsyncSession]

PROBE = "/api/v1/probe/idempotent"
OTHER = "/api/v1/probe/other"
EMPTY = "/api/v1/probe/empty"
PROJECT_PROBE = "/api/v1/projects/{project_id}/probe"
KEY = {"Idempotency-Key": "order-42"}
BODY = {"mode": "ok", "item": "export"}


@dataclass
class Probe:
    """What the probe handler did: how often it ran, and gates to hold a run in flight."""

    runs: int = 0
    entered: dict[int, asyncio.Event] = field(default_factory=dict)
    gates: dict[int, asyncio.Event] = field(default_factory=dict)
    delivered: asyncio.Event = field(default_factory=asyncio.Event)
    background_saw_delivery: bool | None = None

    def entered_event(self, run: int) -> asyncio.Event:
        return self.entered.setdefault(run, asyncio.Event())

    def gate(self, run: int) -> asyncio.Event:
        return self.gates.setdefault(run, asyncio.Event())

    async def wait_until_entered(self, run: int) -> None:
        await asyncio.wait_for(self.entered_event(run).wait(), timeout=5)


async def authorize(request: Request, principal: CurrentPrincipal) -> Principal:
    """Stands in for `require()`: refuses a request that asks to be refused."""
    if request.headers.get("x-probe-deny"):
        raise forbidden()
    return principal


class PathsendResponse(Response):
    """Ends the response with a message that is not a body, as a file sent by path does."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = [(b"content-type", b"application/json")]
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send({"type": "http.response.pathsend", "path": "/dev/null"})


def add_probe_routes(application: FastAPI, probe: Probe) -> None:
    async def after() -> None:
        """Runs after the response, as a route's background task would."""
        try:
            await asyncio.wait_for(probe.delivered.wait(), timeout=2)
            probe.background_saw_delivery = True
        except TimeoutError:
            probe.background_saw_delivery = False

    def streamed(run: int, status_code: int, media_type: str) -> StreamingResponse:
        async def chunks() -> AsyncIterator[bytes]:
            yield b"first"
            # Released by the test once the first chunk has been delivered: a response held back
            # until its end would wait here for nothing.
            await asyncio.wait_for(probe.gate(run).wait(), timeout=2)
            yield b"second"

        return StreamingResponse(chunks(), status_code=status_code, media_type=media_type)

    answers: dict[str, Callable[[int], Any]] = {
        "null": lambda _: JSONResponse(None, status_code=201),
        "text": lambda _: PlainTextResponse('{"looks": "like json"}'),
        "background": lambda run: JSONResponse(
            {"run": run}, status_code=201, background=BackgroundTask(after)
        ),
        "large": lambda run: {"run": run, "blob": "x" * 200_000},
        "too_large": lambda run: JSONResponse(
            {"run": run, "blob": "x" * MAX_KEPT_BODY_BYTES}, status_code=201
        ),
        "deep": lambda _: Response(
            b"[" * 100_000 + b"]" * 100_000, status_code=201, media_type="application/json"
        ),
        "pathsend": lambda _: PathsendResponse(),
        "stream_text": lambda run: streamed(run, 200, "text/plain"),
        "stream_503": lambda run: streamed(run, 503, "application/json"),
    }

    async def act(body: dict[str, Any]) -> Any:
        probe.runs += 1
        run = probe.runs
        probe.entered_event(run).set()
        mode = body.get("mode")
        if mode == "wait":
            await probe.gate(run).wait()
        elif mode == "slow":
            await asyncio.sleep(0.3)
        elif mode == "conflict":
            raise conflict("PROBE_CONFLICT", "The probe was told to refuse.")
        elif mode == "unavailable":
            raise ProblemError(503, "PROBE_UNAVAILABLE", "The probe was told to fail.")
        elif mode == "crash":
            raise RuntimeError("the probe was told to crash")
        elif mode == "flaky" and run == 1:
            raise ProblemError(503, "PROBE_UNAVAILABLE", "The first run of a flaky probe fails.")
        elif mode in answers:
            return answers[mode](run)
        return {"run": run, "echo": body}

    async def handle(
        body: Annotated[dict[str, Any], Body()],
        _access: Annotated[Principal, Depends(idempotent(authorize))],
    ) -> Any:
        return await act(body)

    async def handle_empty(
        _access: Annotated[Principal, Depends(idempotent(authorize))],
    ) -> Response:
        probe.runs += 1
        return Response(status_code=204)

    async def handle_project(
        body: Annotated[dict[str, Any], Body()],
        access: Annotated[Access, Depends(idempotent(require(Permission.PROJECT_READ)))],
    ) -> Any:
        probe.runs += 1
        return {"run": probe.runs, "project": str(access.require_project().id), "echo": body}

    application.add_api_route(PROBE, handle, methods=["POST"], status_code=201)
    application.add_api_route(OTHER, handle, methods=["POST"], status_code=201)
    application.add_api_route(EMPTY, handle_empty, methods=["POST"], status_code=204)
    application.add_api_route(PROJECT_PROBE, handle_project, methods=["POST"], status_code=201)


@pytest.fixture
def probe() -> Probe:
    return Probe()


@pytest.fixture
async def app(settings: Settings, probe: Probe) -> AsyncIterator[Any]:
    """The real app plus the probe routes: `client` and `browser_factory` use it from here on."""
    application = create_app(settings)
    add_probe_routes(application, probe)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def tight_app(settings: Settings, probe: Probe) -> AsyncIterator[Any]:
    """The same app with an idempotency pool of one connection and a short wait for it."""
    tight = settings.model_copy(
        update={"idempotency_pool_size": 1, "idempotency_pool_timeout_seconds": 0.2}
    )
    application = create_app(tight)
    add_probe_routes(application, probe)
    async with application.router.lifespan_context(application):
        yield application


async def stored_rows(session_factory: SessionFactory) -> list[dict[str, Any]]:
    async with session_factory() as db:
        result = await db.execute(
            text(
                "SELECT principal_id, key, status, body, content_type FROM idempotency_keys "
                "ORDER BY principal_id, key"
            )
        )
        return [dict(row._mapping) for row in result]


async def backdate(session_factory: SessionFactory, seconds: int) -> None:
    """Make every stored row look `seconds` older (its expiry stays later than its creation)."""
    async with session_factory() as db:
        await db.execute(
            text(
                "UPDATE idempotency_keys "
                "SET created_at = created_at - make_interval(secs => :seconds)"
            ),
            {"seconds": seconds},
        )
        await db.commit()


def metric(outcome: str) -> float:
    return (
        REGISTRY.get_sample_value("spanlight_idempotency_requests_total", {"outcome": outcome})
        or 0.0
    )


async def post(
    http: httpx.AsyncClient | Browser,
    path: str = PROBE,
    body: Any = BODY,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return await http.post(path, json=body, headers={**KEY, **(headers or {})})


# --- replay ------------------------------------------------------------------------------------


async def test_a_replay_returns_the_stored_status_and_body_and_runs_the_handler_once(
    browser_factory: BrowserFactory, probe: Probe
) -> None:
    ada = await browser_factory("ada@example.com")
    replayed_before = metric("replayed")

    first = await post(ada)
    second = await post(ada)

    assert first.status_code == 201
    assert first.json() == {"run": 1, "echo": BODY}
    assert "idempotent-replayed" not in first.headers
    assert second.status_code == 201
    assert second.json() == first.json()
    assert second.headers["idempotent-replayed"] == "true"
    assert second.headers["content-type"] == first.headers["content-type"] == "application/json"
    assert probe.runs == 1
    assert metric("replayed") == replayed_before + 1


async def test_a_request_without_a_key_runs_every_time_and_stores_nothing(
    browser_factory: BrowserFactory, probe: Probe, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com")

    first = await ada.post(PROBE, json=BODY)
    second = await ada.post(PROBE, json=BODY)

    assert (first.json()["run"], second.json()["run"]) == (1, 2)
    assert "idempotent-replayed" not in second.headers
    assert await stored_rows(session_factory) == []


async def test_a_replay_is_the_stored_body_not_a_new_run_of_the_handler(
    browser_factory: BrowserFactory, probe: Probe, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com")
    await post(ada)

    [row] = await stored_rows(session_factory)

    assert row["principal_id"] == f"user:{ada.user['id']}"
    assert row["key"] == "order-42"
    assert row["status"] == 201
    assert row["body"] == {"run": 1, "echo": BODY}
    assert row["content_type"] == "application/json"


async def test_reordering_and_respacing_the_body_is_the_same_request(
    browser_factory: BrowserFactory, probe: Probe
) -> None:
    ada = await browser_factory("ada@example.com")
    first = await ada.post(
        PROBE,
        content=b'{"mode":"ok","item":"export"}',
        headers={**KEY, "Content-Type": "application/json"},
    )
    second = await ada.post(
        PROBE,
        content=b'{ "item" : "export",\n  "mode" : "ok" }',
        headers={**KEY, "Content-Type": "application/json"},
    )

    assert second.headers["idempotent-replayed"] == "true"
    assert second.json() == first.json()
    assert probe.runs == 1


async def test_a_request_with_no_body_and_a_204_answer_replays_as_empty(
    browser_factory: BrowserFactory, probe: Probe
) -> None:
    ada = await browser_factory("ada@example.com")

    first = await ada.post(EMPTY, headers=KEY)
    second = await ada.post(EMPTY, headers=KEY)

    assert (first.status_code, second.status_code) == (204, 204)
    assert second.content == b""
    assert second.headers["idempotent-replayed"] == "true"
    assert probe.runs == 1


# --- mismatch ----------------------------------------------------------------------------------


async def test_a_different_body_under_the_same_key_is_a_mismatch(
    browser_factory: BrowserFactory, probe: Probe
) -> None:
    ada = await browser_factory("ada@example.com")
    await post(ada)
    mismatch_before = metric("mismatch")

    reused = await post(ada, body={"mode": "ok", "item": "something else"})

    assert reused.status_code == 422
    assert reused.headers["content-type"] == "application/problem+json"
    assert reused.json()["code"] == "IDEMPOTENCY_MISMATCH"
    assert "idempotent-replayed" not in reused.headers
    assert probe.runs == 1
    assert metric("mismatch") == mismatch_before + 1


async def test_the_same_body_on_another_path_under_the_same_key_is_a_mismatch(
    browser_factory: BrowserFactory, probe: Probe
) -> None:
    ada = await browser_factory("ada@example.com")
    await post(ada, path=PROBE)

    reused = await post(ada, path=OTHER)

    assert reused.status_code == 422
    assert reused.json()["code"] == "IDEMPOTENCY_MISMATCH"
    assert probe.runs == 1


# --- in flight ---------------------------------------------------------------------------------


async def test_a_second_request_while_the_first_is_in_flight_is_refused(
    browser_factory: BrowserFactory, probe: Probe
) -> None:
    ada = await browser_factory("ada@example.com")
    waiting = {"mode": "wait"}
    first = asyncio.create_task(post(ada, body=waiting))
    await probe.wait_until_entered(1)
    in_progress_before = metric("in_progress")

    second = await post(ada, body=waiting)

    assert second.status_code == 409
    assert second.json()["code"] == "IDEMPOTENCY_IN_PROGRESS"
    assert metric("in_progress") == in_progress_before + 1
    probe.gate(1).set()
    assert (await first).status_code == 201
    third = await post(ada, body=waiting)
    assert third.headers["idempotent-replayed"] == "true"
    assert probe.runs == 1


async def test_simultaneous_identical_requests_run_the_handler_once(
    browser_factory: BrowserFactory, probe: Probe, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com")

    responses = await asyncio.gather(*(post(ada, body={"mode": "slow"}) for _ in range(5)))

    assert sorted(response.status_code for response in responses) == [201, 409, 409, 409, 409]
    assert probe.runs == 1
    [row] = await stored_rows(session_factory)
    assert row["status"] == 201


async def test_an_in_flight_row_older_than_60_seconds_is_taken_over(
    browser_factory: BrowserFactory, probe: Probe, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com")
    waiting = {"mode": "wait"}
    abandoned = asyncio.create_task(post(ada, body=waiting))
    await probe.wait_until_entered(1)
    await backdate(session_factory, 61)
    taken_over_before = metric("taken_over")

    successor = asyncio.create_task(post(ada, body=waiting))
    await probe.wait_until_entered(2)

    assert metric("taken_over") == taken_over_before + 1
    # The abandoned run finishes first. It no longer owns the row, so it stores nothing, and the
    # key still reads as in flight on behalf of the run that took over.
    probe.gate(1).set()
    assert (await abandoned).json()["run"] == 1
    still_running = await post(ada, body=waiting)
    assert still_running.status_code == 409
    assert (await stored_rows(session_factory))[0]["status"] is None

    probe.gate(2).set()
    assert (await successor).json()["run"] == 2
    replayed = await post(ada, body=waiting)
    assert replayed.headers["idempotent-replayed"] == "true"
    assert replayed.json()["run"] == 2
    assert probe.runs == 2


async def test_an_in_flight_row_younger_than_60_seconds_is_not_taken_over(
    browser_factory: BrowserFactory, probe: Probe, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com")
    waiting = {"mode": "wait"}
    first = asyncio.create_task(post(ada, body=waiting))
    await probe.wait_until_entered(1)
    await backdate(session_factory, 55)

    second = await post(ada, body=waiting)

    assert second.status_code == 409
    probe.gate(1).set()
    await first
    assert probe.runs == 1


# --- failures ----------------------------------------------------------------------------------


async def test_a_handled_server_error_leaves_no_row_so_the_retry_runs(
    browser_factory: BrowserFactory, probe: Probe, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com")
    flaky = {"mode": "flaky"}

    failed = await post(ada, body=flaky)

    assert failed.status_code == 503
    assert await stored_rows(session_factory) == []
    retried = await post(ada, body=flaky)
    assert retried.status_code == 201
    assert "idempotent-replayed" not in retried.headers
    assert probe.runs == 2


async def test_an_unhandled_exception_leaves_no_row_so_the_retry_runs(
    browser_factory: BrowserFactory, probe: Probe, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com")

    with pytest.raises(RuntimeError, match="told to crash"):
        await post(ada, body={"mode": "crash"})

    assert await stored_rows(session_factory) == []
    with pytest.raises(RuntimeError, match="told to crash"):
        await post(ada, body={"mode": "crash"})
    assert probe.runs == 2


async def test_a_client_error_is_stored_and_replayed(
    browser_factory: BrowserFactory, probe: Probe, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com")
    refused = {"mode": "conflict"}

    first = await post(ada, body=refused)
    second = await post(ada, body=refused)

    assert first.status_code == second.status_code == 409
    assert first.json()["code"] == second.json()["code"] == "PROBE_CONFLICT"
    assert second.json() == first.json()
    assert second.headers["idempotent-replayed"] == "true"
    assert second.headers["content-type"] == first.headers["content-type"]
    assert second.headers["content-type"] == "application/problem+json"
    assert probe.runs == 1
    [row] = await stored_rows(session_factory)
    assert (row["status"], row["content_type"]) == (409, "application/problem+json")


async def test_a_request_the_route_rejects_as_invalid_is_stored_like_any_client_error(
    browser_factory: BrowserFactory, probe: Probe
) -> None:
    ada = await browser_factory("ada@example.com")

    first = await post(ada, body=["not", "an", "object"])
    second = await post(ada, body=["not", "an", "object"])

    assert first.status_code == second.status_code == 422
    assert second.json() == first.json()
    assert second.headers["idempotent-replayed"] == "true"
    assert probe.runs == 0


async def test_a_response_that_is_not_declared_json_cannot_be_stored_and_leaves_no_row(
    browser_factory: BrowserFactory, probe: Probe, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com")
    odd = {"mode": "text"}

    first = await post(ada, body=odd)
    second = await post(ada, body=odd)

    assert (first.text, second.text) == ('{"looks": "like json"}',) * 2
    assert "idempotent-replayed" not in second.headers
    assert await stored_rows(session_factory) == []
    assert probe.runs == 2


async def test_a_different_body_is_a_mismatch_even_while_the_first_request_runs(
    browser_factory: BrowserFactory, probe: Probe
) -> None:
    ada = await browser_factory("ada@example.com")
    first = asyncio.create_task(post(ada, body={"mode": "wait"}))
    await probe.wait_until_entered(1)

    reused = await post(ada, body={"mode": "ok", "item": "something else"})

    assert reused.status_code == 422
    assert reused.json()["code"] == "IDEMPOTENCY_MISMATCH"
    probe.gate(1).set()
    await first


async def call_asgi(
    app: Any,
    api_key: str,
    body: bytes,
    on_message: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> list[dict[str, Any]]:
    """POST to the app without httpx, which would hide when each message is delivered."""
    delivered: list[dict[str, Any]] = []

    sent_body = False

    async def receive() -> dict[str, Any]:
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": body, "more_body": False}
        # A streaming response listens for the client to leave. This client stays until the
        # response is over; answering at once would make that listener spin without yielding.
        await asyncio.Event().wait()
        raise AssertionError("unreachable")  # pragma: no cover

    async def send(message: dict[str, Any]) -> None:
        delivered.append(message)
        if on_message is not None:
            await on_message(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": PROBE,
        "raw_path": PROBE.encode(),
        "query_string": b"",
        "headers": [
            (b"host", b"testserver"),
            (b"authorization", f"Bearer {api_key}".encode()),
            (b"content-type", b"application/json"),
            (b"idempotency-key", b"order-42"),
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    return delivered


def is_last_body(message: dict[str, Any]) -> bool:
    return message["type"] == "http.response.body" and not message.get("more_body")


async def test_the_answer_is_stored_before_the_client_receives_its_last_byte(
    app: Any, browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    """A client that retries the instant it has an answer must find the answer, not a 409."""
    workspace = await create_workspace(await browser_factory("ada@example.com"))
    seen: list[list[int | None]] = []

    async def record(message: dict[str, Any]) -> None:
        if is_last_body(message):
            seen.append([row["status"] for row in await stored_rows(session_factory)])

    await call_asgi(app, workspace.api_key, b'{"mode": "ok"}', record)

    assert seen == [[201]]


async def test_a_background_task_does_not_hold_back_the_answer(
    app: Any, browser_factory: BrowserFactory, session_factory: SessionFactory, probe: Probe
) -> None:
    """Starlette runs a response's background tasks after its last byte; so must the storing."""
    workspace = await create_workspace(await browser_factory("ada@example.com"))
    stored_when_delivered: list[list[int | None]] = []

    async def record(message: dict[str, Any]) -> None:
        if is_last_body(message):
            stored_when_delivered.append(
                [row["status"] for row in await stored_rows(session_factory)]
            )
            probe.delivered.set()

    await call_asgi(app, workspace.api_key, b'{"mode": "background"}', record)

    assert probe.background_saw_delivery is True
    assert stored_when_delivered == [[201]]


async def test_a_failure_to_store_the_answer_does_not_fail_the_request(
    browser_factory: BrowserFactory,
    probe: Probe,
    session_factory: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(*_: Any, **__: Any) -> bool:
        raise ConnectionError("the database went away")

    monkeypatch.setattr("app.core.idempotency.complete", broken)
    ada = await browser_factory("ada@example.com")

    response = await post(ada)

    assert response.status_code == 201
    # The key stays in flight; a retry is refused until the minute is up and then runs again.
    assert [row["status"] for row in await stored_rows(session_factory)] == [None]
    assert (await post(ada)).status_code == 409


async def test_a_failure_to_release_the_key_does_not_change_the_answer(
    browser_factory: BrowserFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*_: Any, **__: Any) -> bool:
        raise ConnectionError("the database went away")

    monkeypatch.setattr("app.core.idempotency.release", broken)
    ada = await browser_factory("ada@example.com")

    response = await post(ada, body={"mode": "unavailable"})

    assert response.status_code == 503
    assert response.json()["code"] == "PROBE_UNAVAILABLE"


async def test_a_bare_json_null_answer_cannot_be_kept_faithfully_and_leaves_no_row(
    browser_factory: BrowserFactory, probe: Probe, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com")

    first = await post(ada, body={"mode": "null"})
    second = await post(ada, body={"mode": "null"})

    assert (first.json(), second.json()) == (None, None)
    assert "idempotent-replayed" not in second.headers
    assert await stored_rows(session_factory) == []


# --- the key itself ----------------------------------------------------------------------------


async def test_a_key_of_128_characters_is_accepted(
    browser_factory: BrowserFactory, probe: Probe, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com")

    response = await ada.post(PROBE, json=BODY, headers={"Idempotency-Key": "k" * 128})

    assert response.status_code == 201
    [row] = await stored_rows(session_factory)
    assert row["key"] == "k" * 128


@pytest.mark.parametrize(
    "key",
    [b"k" * 129, b"", b"two words", b"tab\there", b"caf\xc3\xa9"],
    ids=["too-long", "empty", "space", "tab", "non-ascii"],
)
async def test_an_invalid_key_is_422_and_nothing_runs_or_is_stored(
    browser_factory: BrowserFactory,
    probe: Probe,
    session_factory: SessionFactory,
    key: bytes,
) -> None:
    ada = await browser_factory("ada@example.com")
    csrf = ada.http.cookies["spl_csrf"].encode()

    response = await ada.http.post(
        PROBE, json=BODY, headers=[(b"idempotency-key", key), (b"x-csrf-token", csrf)]
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert [error["field"] for error in body["errors"]] == ["Idempotency-Key"]
    assert probe.runs == 0
    assert await stored_rows(session_factory) == []


# --- who the key belongs to --------------------------------------------------------------------


async def test_a_session_and_a_token_of_the_same_user_share_their_keys(
    browser_factory: BrowserFactory, client: httpx.AsyncClient, probe: Probe
) -> None:
    ada = await browser_factory("ada@example.com")
    token = await create_token(ada)

    from_session = await post(ada)
    from_token = await client.post(PROBE, json=BODY, headers={**KEY, **bearer(token["token"])})

    assert from_token.status_code == 201
    assert from_token.headers["idempotent-replayed"] == "true"
    assert from_token.json() == from_session.json()
    assert probe.runs == 1


async def test_another_user_with_the_same_key_is_independent(
    browser_factory: BrowserFactory, probe: Probe
) -> None:
    ada = await browser_factory("ada@example.com")
    grace = await browser_factory("grace@example.com")

    for_ada = await post(ada)
    for_grace = await post(grace)

    assert (for_ada.json()["run"], for_grace.json()["run"]) == (1, 2)
    assert "idempotent-replayed" not in for_grace.headers


async def test_an_api_key_has_a_keyspace_of_its_own(
    browser_factory: BrowserFactory,
    client: httpx.AsyncClient,
    probe: Probe,
    session_factory: SessionFactory,
) -> None:
    ada = await browser_factory("ada@example.com")
    workspace = await create_workspace(ada)

    from_session = await post(ada)
    from_key = await client.post(PROBE, json=BODY, headers={**KEY, **bearer(workspace.api_key)})
    key_again = await client.post(PROBE, json=BODY, headers={**KEY, **bearer(workspace.api_key)})

    assert from_session.json()["run"] == 1
    assert from_key.json()["run"] == 2
    assert key_again.headers["idempotent-replayed"] == "true"
    principals = [row["principal_id"] for row in await stored_rows(session_factory)]
    assert len(principals) == 2
    assert {p.split(":")[0] for p in principals} == {"user", "key"}


def test_principal_key_names_the_user_for_sessions_and_tokens_and_the_key_for_api_keys() -> None:
    user_id, key_id = uuid.uuid4(), uuid.uuid4()
    user = User(id=user_id)

    assert principal_key(SessionPrincipal(user=user, session=Session())) == f"user:{user_id}"
    token = TokenPrincipal(user=user, token_id=uuid.uuid4(), scope=TokenScope.READ)
    assert principal_key(token) == f"user:{user_id}"
    api_key = ApiKeyPrincipal(key_id=key_id, project_id=uuid.uuid4(), scopes=frozenset())
    assert principal_key(api_key) == f"key:{key_id}"


# --- nothing is reserved before the caller is let in -------------------------------------------


async def test_a_request_with_no_credentials_reserves_nothing(
    client: httpx.AsyncClient, probe: Probe, session_factory: SessionFactory
) -> None:
    no_session = await client.post(PROBE, json=BODY, headers=KEY)
    bad_key = await client.post(PROBE, json=BODY, headers={**KEY, **bearer("spl_live_nope")})

    assert (no_session.status_code, bad_key.status_code) == (401, 401)
    assert probe.runs == 0
    assert await stored_rows(session_factory) == []


async def test_a_request_the_route_authorizes_away_reserves_nothing(
    browser_factory: BrowserFactory, probe: Probe, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com")

    denied = await post(ada, headers={"X-Probe-Deny": "yes"})

    assert denied.status_code == 403
    assert probe.runs == 0
    assert await stored_rows(session_factory) == []


async def test_a_caller_the_permission_check_refuses_never_reserves_a_key(
    browser_factory: BrowserFactory, probe: Probe, session_factory: SessionFactory
) -> None:
    owner = await browser_factory("owner@example.com")
    stranger = await browser_factory("stranger@example.com")
    workspace = await create_workspace(owner)

    refused = await post(stranger, path=PROJECT_PROBE.format(project_id=workspace.project_id))

    assert refused.status_code == 404
    assert probe.runs == 0
    assert await stored_rows(session_factory) == []


async def test_a_replay_is_authorized_like_the_request_it_replays(
    browser_factory: BrowserFactory, probe: Probe
) -> None:
    owner = await browser_factory("owner@example.com")
    member = await browser_factory("member@example.com")
    workspace = await create_workspace(owner)
    await join_with_role(owner, member, workspace.org_id, "viewer")
    path = PROJECT_PROBE.format(project_id=workspace.project_id)

    original = await post(member, path=path)
    replay = await post(member, path=path)
    assert original.status_code == 201
    assert replay.headers["idempotent-replayed"] == "true"

    removed = await owner.delete(f"/api/v1/orgs/{workspace.org_id}/members/{member.user['id']}")
    assert removed.status_code == 204
    after_removal = await post(member, path=path)

    assert after_removal.status_code == 404
    assert "idempotent-replayed" not in after_removal.headers
    assert after_removal.json()["code"] == "NOT_FOUND"
    assert probe.runs == 1


# --- expiry ------------------------------------------------------------------------------------


async def test_an_expired_row_that_is_not_pruned_yet_is_treated_as_new(
    browser_factory: BrowserFactory, probe: Probe, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com")
    await post(ada)
    async with session_factory() as db:
        await db.execute(
            text(
                "UPDATE idempotency_keys SET created_at = now() - interval '25 hours', "
                "expires_at = now() - interval '1 hour'"
            )
        )
        await db.commit()

    again = await post(ada, body={"mode": "ok", "item": "a new request"})

    assert again.status_code == 201
    assert again.json()["run"] == 2
    assert "idempotent-replayed" not in again.headers
    [row] = await stored_rows(session_factory)
    assert row["body"]["echo"]["item"] == "a new request"
    async with session_factory() as db:
        remaining = (
            await db.execute(
                text("SELECT expires_at > now() + interval '23 hours' FROM idempotency_keys")
            )
        ).scalar_one()
    assert remaining is True


async def test_a_new_row_expires_a_day_after_it_was_made(
    browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com")
    await post(ada)

    async with session_factory() as db:
        lifetime = (
            await db.execute(
                text("SELECT extract(epoch FROM expires_at - created_at) FROM idempotency_keys")
            )
        ).scalar_one()

    assert lifetime == 24 * 3600


# --- the pool of its own -----------------------------------------------------------------------


class RecordingFactory:
    """Wraps a session factory and remembers which engine each session it made is bound to."""

    def __init__(self, inner: SessionFactory) -> None:
        self.inner = inner
        self.binds: list[Any] = []

    def __call__(self) -> AsyncSession:
        session = self.inner()
        self.binds.append(session.bind)
        return session


async def test_the_store_runs_on_a_pool_of_its_own(
    app: Any, browser_factory: BrowserFactory, settings: Settings
) -> None:
    recording = RecordingFactory(app.state.idempotency_session_factory)
    app.state.idempotency_session_factory = recording
    ada = await browser_factory("ada@example.com")

    await post(ada)  # reserves, then completes
    await post(ada)  # reserves, and is told to replay

    assert len(recording.binds) == 3
    assert all(bind is app.state.idempotency_engine for bind in recording.binds)
    assert app.state.idempotency_engine is not app.state.engine
    pool = app.state.idempotency_engine.pool
    assert (pool.size(), pool.timeout()) == (
        settings.idempotency_pool_size,
        settings.idempotency_pool_timeout_seconds,
    )


async def test_a_busy_idempotency_pool_fails_fast_and_leaves_other_requests_alone(
    tight_app: Any, probe: Probe
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=tight_app),
        base_url=TEST_ORIGIN,
        headers={"Origin": TEST_ORIGIN},
    ) as http:
        signup = await http.post(
            "/api/v1/auth/signup",
            json={"email": "ada@example.com", "password": "correct horse battery", "name": "Ada"},
        )
        ada = Browser(http=http, user=signup.json())

        async with tight_app.state.idempotency_engine.connect():  # the pool's only connection
            assert (await ada.get("/api/v1/auth/me")).status_code == 200
            started = time.monotonic()
            # The pool timeout surfaces as the documented "database is behind" 503, not a crash.
            busy = await post(ada)
            assert time.monotonic() - started < 3
            assert busy.status_code == 503
            assert busy.json()["code"] == "SERVICE_UNAVAILABLE"
            assert int(busy.headers["Retry-After"]) > 0
        assert probe.runs == 0

        assert (await post(ada)).status_code == 201


async def test_both_engines_are_disposed_with_the_app(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    disposed: list[AsyncEngine] = []
    dispose = AsyncEngine.dispose

    async def spy(self: AsyncEngine, close: bool = True) -> None:
        disposed.append(self)
        await dispose(self, close)

    monkeypatch.setattr(AsyncEngine, "dispose", spy)
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        pass

    assert application.state.engine in disposed
    assert application.state.idempotency_engine in disposed


def test_the_pool_settings_have_defaults_and_a_blank_value_means_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IDEMPOTENCY_POOL_SIZE", "")
    monkeypatch.setenv("IDEMPOTENCY_POOL_TIMEOUT_SECONDS", "")

    configured = Settings(_env_file=None)

    assert (configured.idempotency_pool_size, configured.idempotency_pool_timeout_seconds) == (5, 5)


@pytest.mark.parametrize(
    ("name", "value"),
    [("IDEMPOTENCY_POOL_SIZE", "0"), ("IDEMPOTENCY_POOL_TIMEOUT_SECONDS", "0")],
)
def test_a_pool_that_could_never_serve_a_request_is_refused_at_startup(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name.lower()):
        Settings(_env_file=None)


# --- what is held and what is not --------------------------------------------------------------


async def test_a_response_under_the_cap_is_kept_however_large(
    browser_factory: BrowserFactory, probe: Probe
) -> None:
    ada = await browser_factory("ada@example.com")

    first = await post(ada, body={"mode": "large"})
    second = await post(ada, body={"mode": "large"})

    assert second.headers["idempotent-replayed"] == "true"
    assert second.json() == first.json()
    assert len(second.json()["blob"]) == 200_000
    assert probe.runs == 1


async def test_a_response_over_the_cap_is_delivered_whole_and_leaves_no_row(
    browser_factory: BrowserFactory, probe: Probe, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com")

    first = await post(ada, body={"mode": "too_large"})
    second = await post(ada, body={"mode": "too_large"})

    assert len(first.json()["blob"]) == len(second.json()["blob"]) == MAX_KEPT_BODY_BYTES
    assert "idempotent-replayed" not in second.headers
    assert await stored_rows(session_factory) == []
    assert probe.runs == 2


async def test_a_response_too_deeply_nested_to_parse_is_delivered_and_not_kept(
    browser_factory: BrowserFactory, probe: Probe, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com")

    response = await post(ada, body={"mode": "deep"})

    assert response.status_code == 201
    assert len(response.content) == 200_000
    assert await stored_rows(session_factory) == []


@pytest.mark.parametrize("mode", ["stream_text", "stream_503"])
async def test_a_response_that_cannot_be_kept_is_passed_on_as_it_comes(
    mode: str,
    app: Any,
    browser_factory: BrowserFactory,
    probe: Probe,
    session_factory: SessionFactory,
) -> None:
    """Decided at the first message: a 5xx or a non-JSON type is not buffered until its end."""
    workspace = await create_workspace(await browser_factory("ada@example.com"))
    first_chunk_was_early: list[bool] = []

    async def on_message(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body" and message["body"] == b"first":
            first_chunk_was_early.append(not probe.gate(1).is_set())
            probe.gate(1).set()  # lets the response produce its second chunk

    messages = await call_asgi(
        app, workspace.api_key, json.dumps({"mode": mode}).encode(), on_message
    )

    assert first_chunk_was_early == [True]
    bodies = [m["body"] for m in messages if m["type"] == "http.response.body"]
    assert b"".join(bodies) == b"firstsecond"
    assert await stored_rows(session_factory) == []


async def test_a_message_that_is_not_a_body_ends_the_hold_and_is_delivered(
    app: Any, browser_factory: BrowserFactory, session_factory: SessionFactory
) -> None:
    workspace = await create_workspace(await browser_factory("ada@example.com"))

    messages = await call_asgi(app, workspace.api_key, b'{"mode": "pathsend"}')

    assert [m["type"] for m in messages] == ["http.response.start", "http.response.pathsend"]
    assert await stored_rows(session_factory) == []


async def test_simultaneous_requests_after_an_abandoned_one_start_exactly_one_new_run(
    browser_factory: BrowserFactory, probe: Probe, session_factory: SessionFactory
) -> None:
    ada = await browser_factory("ada@example.com")
    waiting = {"mode": "wait"}
    abandoned = asyncio.create_task(post(ada, body=waiting))
    await probe.wait_until_entered(1)
    await backdate(session_factory, 61)

    contenders = [asyncio.create_task(post(ada, body=waiting)) for _ in range(4)]
    await probe.wait_until_entered(2)

    refused: list[int] = []
    for finished in asyncio.as_completed(contenders):
        refused.append((await asyncio.wait_for(finished, timeout=5)).status_code)
        if len(refused) == 3:
            break
    assert refused == [409, 409, 409]
    probe.gate(1).set()
    probe.gate(2).set()
    results = await asyncio.gather(abandoned, *contenders)

    assert sorted(response.status_code for response in results[1:]) == [201, 409, 409, 409]
    assert probe.runs == 2


# --- fingerprint -------------------------------------------------------------------------------


def test_the_fingerprint_covers_the_method_the_path_and_the_canonical_body() -> None:
    base = request_fingerprint("POST", "/a", b'{"x": 1, "y": [1, 2]}')

    assert len(base) == 32
    assert request_fingerprint("POST", "/a", b'{"y":[1,2],"x":1}') == base
    assert request_fingerprint("post", "/a", b'{"y":[1,2],"x":1}') == base
    assert request_fingerprint("PUT", "/a", b'{"x": 1, "y": [1, 2]}') != base
    assert request_fingerprint("POST", "/b", b'{"x": 1, "y": [1, 2]}') != base
    assert request_fingerprint("POST", "/a", b'{"x": 2, "y": [1, 2]}') != base
    assert request_fingerprint("POST", "/a", b'{"x": 1, "y": [2, 1]}') != base


def test_the_fingerprint_of_a_body_that_is_not_json_is_its_bytes() -> None:
    assert request_fingerprint("POST", "/a", b"") == request_fingerprint("POST", "/a", b"")
    assert request_fingerprint("POST", "/a", b"not json") != request_fingerprint(
        "POST", "/a", b"not json!"
    )
    assert request_fingerprint("POST", "/a", b"") != request_fingerprint("POST", "/a", b"null")


# --- wiring ------------------------------------------------------------------------------------


async def test_the_dependency_refuses_to_run_without_the_middleware() -> None:
    bare = FastAPI()
    bare.dependency_overrides[current_principal] = lambda: ApiKeyPrincipal(
        key_id=uuid.uuid4(), project_id=uuid.uuid4(), scopes=frozenset()
    )

    async def allow() -> str:
        return "allowed"

    async def handle(access: Annotated[str, Depends(idempotent(allow))]) -> dict[str, str]:
        return {"access": access}

    bare.add_api_route("/x", handle, methods=["POST"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=bare), base_url="http://testserver"
    ) as http:
        # Without a key there is nothing to track, so the missing middleware goes unnoticed.
        assert (await http.post("/x")).json() == {"access": "allowed"}
        with pytest.raises(RuntimeError, match="install_idempotency"):
            await http.post("/x", headers=KEY)


def test_a_request_body_too_deeply_nested_to_parse_is_hashed_as_its_bytes() -> None:
    nested = b"[" * 100_000 + b"]" * 100_000

    assert request_fingerprint("POST", "/a", nested) == request_fingerprint("POST", "/a", nested)
    assert request_fingerprint("POST", "/a", nested) != request_fingerprint("POST", "/a", b"[]")

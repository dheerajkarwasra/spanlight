"""Sentry error reporting: the api and the worker both start it from SENTRY_DSN.

These use the real `sentry_sdk` client. Only its transport is swapped for a recorder, so no
event leaves the machine.
"""

import copy
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any, NamedTuple

import httpx
import pytest
import sentry_sdk
from fastapi import Request
from pydantic import BaseModel
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport
from sentry_sdk.types import Event
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings, get_settings
from app.core.sentry import before_send, init_sentry
from app.jobs import worker
from app.main import create_app

TEST_DSN = "https://public@sentry.invalid/1"
TEST_ORIGIN = "http://testserver"
# Built in two parts so the text is never a literal near the calls below: an event also carries
# the source lines and local variables of the test frames that sit under the failing request.
CUSTOMER_TEXT = "ada@example.com asks why " + "her invoice was charged twice"
CUSTOMER_BODY = {"content": CUSTOMER_TEXT}
# An invite token, split like CUSTOMER_TEXT. Links mailed before the token moved to the fragment
# open /invite/<token>, so the browser can send it as the Referer of the API calls that page makes.
INVITE_TOKEN = "invite-token-" + "s3cr3t-value"
UNREACHABLE_DATABASE = "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none"


class RecordingTransport(Transport):
    """Keeps the events the client would have sent."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict[str, Any]] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        event = envelope.get_event()
        if event is not None:
            self.events.append(dict(event))


class Batch(BaseModel):
    content: str


class ReportingApi(NamedTuple):
    """An api started with a Sentry DSN, and the events its Sentry client would have sent."""

    http: httpx.AsyncClient
    events: list[dict[str, Any]]


@pytest.fixture(autouse=True)
def fresh_sentry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Each test starts and ends with Sentry uninitialised, as in a process without a DSN."""
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    sentry_sdk.get_global_scope().set_client(None)
    yield
    sentry_sdk.get_client().close(timeout=0)
    sentry_sdk.get_global_scope().set_client(None)


@pytest.fixture
async def reporting_api(settings: Settings) -> AsyncIterator[ReportingApi]:
    application = create_app(settings.model_copy(update={"sentry_dsn": TEST_DSN}))
    recorder = RecordingTransport()
    sentry_sdk.get_client().transport = recorder

    async def explode() -> None:
        raise RuntimeError("deliberate failure")

    async def explode_with_model(batch: Batch) -> None:
        raise RuntimeError(f"deliberate failure with {len(batch.content)} characters")

    async def explode_with_payload(request: Request) -> None:
        payload = await request.json()
        raise RuntimeError(f"deliberate failure with {len(payload)} keys")

    application.add_api_route("/api/test-explode", explode, methods=["GET", "POST"])
    application.add_api_route("/api/test-explode-model", explode_with_model, methods=["POST"])
    application.add_api_route("/api/test-explode-payload", explode_with_payload, methods=["POST"])
    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url=TEST_ORIGIN, headers={"Origin": TEST_ORIGIN}
    ) as http:
        yield ReportingApi(http, recorder.events)


def test_init_sentry_starts_the_client_without_personal_data_or_request_bodies() -> None:
    started = init_sentry(Settings(sentry_dsn=TEST_DSN, _env_file=None))
    client = sentry_sdk.get_client()
    assert started is True
    assert client.is_active()
    assert client.options["dsn"] == TEST_DSN
    assert client.options["send_default_pii"] is False
    assert client.options["max_request_body_size"] == "never"
    assert client.options["include_local_variables"] is False
    assert client.options["before_send"] is before_send


def test_init_sentry_does_nothing_without_a_dsn() -> None:
    assert init_sentry(Settings(sentry_dsn=None, _env_file=None)) is False
    assert not sentry_sdk.get_client().is_active()


def test_before_send_drops_the_query_string_the_url_query_and_the_referer() -> None:
    event: Event = {
        "message": "boom",
        "request": {
            "method": "GET",
            "url": "https://spanlight.example/api/v1/invites/preview?token=abc#frag",
            "query_string": "token=abc",
            "headers": {
                "Referer": "https://spanlight.example/invite/abc",
                "host": "spanlight.example",
            },
        },
    }
    original = copy.deepcopy(event)
    assert before_send(event, {}) == {
        "message": "boom",
        "request": {
            "method": "GET",
            "url": "https://spanlight.example/api/v1/invites/preview",
            "headers": {"host": "spanlight.example"},
        },
    }
    assert event == original  # the input is not modified


def test_before_send_matches_the_referer_header_in_any_case() -> None:
    event: Event = {"request": {"headers": {"referer": "https://x.example/invite/abc"}}}
    assert before_send(event, {}) == {"request": {"headers": {}}}


@pytest.mark.parametrize(
    "event",
    [
        {"message": "worker job_failed"},
        {"request": {}},
        {"request": {"url": "https://spanlight.example/health/ready", "headers": [["a", "b"]]}},
    ],
)
def test_before_send_leaves_events_without_those_fields_alone(event: Event) -> None:
    assert before_send(copy.deepcopy(event), {}) == event


async def test_worker_entrypoint_starts_sentry_before_touching_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SENTRY_DSN", TEST_DSN)
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_DATABASE)
    get_settings.cache_clear()
    try:
        # The database is unreachable, so the entrypoint fails right after its setup.
        with pytest.raises(SQLAlchemyError):
            await worker.main()
        assert sentry_sdk.get_client().is_active()
    finally:
        get_settings.cache_clear()


async def test_worker_entrypoint_leaves_sentry_off_without_a_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SENTRY_DSN", "")
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_DATABASE)
    get_settings.cache_clear()
    try:
        with pytest.raises(SQLAlchemyError):
            await worker.main()
        assert not sentry_sdk.get_client().is_active()
    finally:
        get_settings.cache_clear()


def test_create_app_starts_sentry_from_the_dsn(settings: Settings) -> None:
    create_app(settings.model_copy(update={"sentry_dsn": TEST_DSN}))
    client = sentry_sdk.get_client()
    assert client.is_active()
    assert client.options["dsn"] == TEST_DSN
    assert client.options["send_default_pii"] is False


def test_create_app_leaves_sentry_off_without_a_dsn(settings: Settings) -> None:
    create_app(settings.model_copy(update={"sentry_dsn": None}))
    assert not sentry_sdk.get_client().is_active()


async def test_client_errors_are_not_reported(reporting_api: ReportingApi) -> None:
    unknown_route = await reporting_api.http.get("/api/v1/definitely-not-a-route")
    unauthenticated = await reporting_api.http.get(
        "/api/v1/projects/00000000-0000-0000-0000-000000000000/traces"
    )
    assert (unknown_route.status_code, unauthenticated.status_code) == (404, 401)
    assert reporting_api.events == []


async def test_server_errors_are_reported(reporting_api: ReportingApi) -> None:
    response = await reporting_api.http.get("/api/test-explode")
    assert response.status_code == 500
    reported = [
        value["type"]
        for event in reporting_api.events
        for value in event.get("exception", {}).get("values", [])
    ]
    assert "RuntimeError" in reported


@pytest.mark.parametrize(
    "route", ["/api/test-explode", "/api/test-explode-model", "/api/test-explode-payload"]
)
async def test_request_bodies_are_never_sent(reporting_api: ReportingApi, route: str) -> None:
    """A 5xx during ingest or login must not ship customers' prompts or emails to Sentry.

    The SDK has two ways to attach a body: the request data of the event, and the local
    variables of the stack frames (FastAPI's own `body`, a handler's `payload`). Both must be off.
    """
    response = await reporting_api.http.post(route, json=CUSTOMER_BODY)
    assert response.status_code == 500
    assert any("exception" in event for event in reporting_api.events)
    for event in reporting_api.events:
        assert not event.get("request", {}).get("data")
        assert CUSTOMER_TEXT not in json.dumps(event)


async def test_invite_tokens_in_the_url_never_reach_sentry(reporting_api: ReportingApi) -> None:
    """A token in a query string or in the Referer of an old /invite/<token> page is dropped."""
    response = await reporting_api.http.get(
        "/api/test-explode",
        params={"token": INVITE_TOKEN},
        headers={"Referer": f"{TEST_ORIGIN}/invite/{INVITE_TOKEN}"},
    )
    assert response.status_code == 500
    assert any("exception" in event for event in reporting_api.events)
    for event in reporting_api.events:
        assert INVITE_TOKEN not in json.dumps(event)
        assert "query_string" not in event.get("request", {})
        assert event.get("request", {}).get("url") == f"{TEST_ORIGIN}/api/test-explode"

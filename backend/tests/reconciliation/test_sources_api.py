"""Billing sources: create, read, edit, rotate and delete them (spec §8.2).

Failure modes: an admin key comes back in a response, an audit event or a log line; a key of
the wrong provider is stored and fails every night; a source is mapped to another organization's
project, or a project to two accounts of one provider; the check's failure loses the key the
user just pasted, or stores a provider's response body as `last_error`; a server without
CREDENTIALS_KEYS stores a key it cannot seal.
"""

import importlib
import io
import logging
import re
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.core.logging import configure_logging
from app.db.models import AuditEvent
from tests.helpers import Browser, create_project, create_workspace
from tests.reconciliation.fetch_support import (
    ANTHROPIC_COST_PATH,
    ANTHROPIC_KEY,
    OPENAI_COST_PATH,
    Reply,
)
from tests.reconciliation.sources_support import (
    ANTHROPIC_KEY_2,
    NEEDS_KEYS,
    NOT_ADMIN_KEY,
    ProviderApi,
    answering,
    create_source,
    running_app,
    secret_windows,
    signed_up,
    source_body,
    sources_url,
)

# The fixed vocabulary of `last_error` (Global Constraints), besides an HTTP status line.
LAST_ERROR_PHRASES = {
    "timeout",
    "connection error",
    "host not found",
    "blocked address",
    "key cannot be decrypted",
    "CREDENTIALS_KEYS is not set",
    "too many pages",
    "response too large",
    "unsupported currency",
    "invalid response",
    "source disabled",  # a run only: a manual run whose source was disabled before it ran
}
STATUS_LINE = re.compile(r"^[1-5]\d\d [A-Za-z][A-Za-z '-]*$")


@pytest.fixture
def provider_api() -> ProviderApi:
    return answering()


@pytest.fixture
async def app(gateway_settings: Settings, provider_api: ProviderApi) -> AsyncIterator[Any]:
    """The app `browser_factory` signs people in to: it seals keys, and its provider is scripted."""
    async with running_app(gateway_settings, provider_api) as application:
        yield application


@pytest.fixture
async def owner(browser_factory: Any) -> Browser:
    browser: Browser = await browser_factory("owner@example.com")
    return browser


@pytest.fixture
async def org(owner: Browser) -> dict[str, str]:
    workspace = await create_workspace(owner)
    second = await create_project(owner, workspace.org_id, "Search")
    return {"org_id": workspace.org_id, "p1": workspace.project_id, "p2": second}


@pytest.fixture
def app_logs(app: Any, caplog: pytest.LogCaptureFixture) -> Iterator[io.StringIO]:
    """Logging routed as in production, at DEBUG, into a buffer (after the app configured it)."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    buffer = io.StringIO()
    configure_logging("DEBUG", json=True, stream=buffer)
    root.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG)
    yield buffer
    root.handlers = saved_handlers
    root.setLevel(saved_level)


async def audit_rows(
    session_factory: async_sessionmaker[AsyncSession], org_id: str
) -> list[AuditEvent]:
    async with session_factory() as db:
        rows = await db.scalars(
            select(AuditEvent)
            .where(AuditEvent.org_id == org_id, AuditEvent.target_type == "billing_source")
            .order_by(AuditEvent.created_at, AuditEvent.id)
        )
        return list(rows)


def field_errors(response: Any) -> list[tuple[str, str]]:
    return [(error["field"], error["message"]) for error in response.json().get("errors") or []]


async def test_create_anthropic_source_checks_the_key(
    owner: Browser,
    org: dict[str, str],
    provider_api: ProviderApi,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    response = await owner.post(
        sources_url(org["org_id"]), json=source_body(project_ids=[org["p1"]])
    )

    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["kind"] == "anthropic_admin_api"
    assert body["provider"] == "anthropic"
    assert body["name"] == "Prod account"
    assert (body["status"], body["last_error"], body["enabled"]) == ("ok", None, True)
    assert (body["key_set"], body["key_id"]) == (True, "gateway-test")
    assert body["last_fetched_at"] is not None
    assert body["projects"] == [{"id": org["p1"], "name": "Chatbot", "slug": "chatbot"}]
    assert body["created_by"]["id"] == owner.user["id"]
    assert "admin_key" not in body
    assert "ciphertext" not in body
    # One check: a one-bucket fetch of yesterday, with the key the user sent.
    assert provider_api.paths() == [ANTHROPIC_COST_PATH]
    assert provider_api.requests[0].headers["x-api-key"] == ANTHROPIC_KEY
    assert provider_api.requests[0].url.params["limit"] == "1"

    [event] = await audit_rows(session_factory, org["org_id"])
    assert event.action == "billing_source.create"
    assert event.target_id == body["id"]
    assert event.metadata_ == {
        "kind": "anthropic_admin_api",
        "provider": "anthropic",
        "project_ids": [org["p1"]],
    }

    listed = await owner.get(sources_url(org["org_id"]))
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    assert listed.json() == [body]
    one = await owner.get(f"{sources_url(org['org_id'])}/{body['id']}")
    assert one.status_code == 200
    assert one.headers["cache-control"] == "no-store"
    assert one.json() == body


async def test_openai_source_checks_against_openai(
    owner: Browser, org: dict[str, str], provider_api: ProviderApi
) -> None:
    created = await create_source(owner, org["org_id"], kind="openai_costs_api")
    assert (created["provider"], created["status"]) == ("openai", "ok")
    assert provider_api.paths() == [OPENAI_COST_PATH]


@pytest.mark.parametrize(
    ("kind", "admin_key"),
    [
        ("anthropic_admin_api", "sk-admin-Vb3Nq8ZtLx5MwPc1RkYh7GdJs2FeUa9T"),
        ("anthropic_admin_api", "sk-ant-api03-Q7xv93LmZp4RtKc8WnBy2HdJfE6gUaS1"),
        ("openai_costs_api", "sk-proj-Vb3Nq8ZtLx5MwPc1RkYh7GdJs2FeUa9T"),
        ("openai_costs_api", "sk-ant-admin01-Q7xv93LmZp4RtKc8WnBy2HdJfE6gUaS1"),
        ("anthropic_admin_api", "sk-ant-admin01-Q7xv93Lm\x00Zp4RtKc8WnBy2HdJ"),
        ("anthropic_admin_api", "sk-ant-admin01-Q7xv93Lm\nZp4RtKc8WnBy2HdJ"),
        ("anthropic_admin_api", "sk-ant-admin01-Q7xv93Lmé4RtKc8WnBy2HdJ"),
        ("anthropic_admin_api", "sk-ant-admin01-Q7xv93Lm Zp4RtKc8WnBy2HdJ"),
        ("openai_costs_api", "   "),
    ],
    ids=[
        "openai-key-on-anthropic",
        "anthropic-api-key",
        "openai-project-key",
        "anthropic-key-on-openai",
        "nul",
        "newline-inside",
        "non-ascii",
        "space-inside",
        "blank",
    ],
)
async def test_a_key_that_is_not_an_admin_key_is_422(
    owner: Browser, org: dict[str, str], provider_api: ProviderApi, kind: str, admin_key: str
) -> None:
    response = await owner.post(
        sources_url(org["org_id"]), json=source_body(kind, admin_key=admin_key)
    )

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert field_errors(response) == [("admin_key", NOT_ADMIN_KEY)]
    assert admin_key.strip() not in response.text or not admin_key.strip()
    assert provider_api.requests == []
    assert (await owner.get(sources_url(org["org_id"]))).json() == []


async def test_a_key_is_trimmed_and_capped(
    owner: Browser, org: dict[str, str], provider_api: ProviderApi
) -> None:
    padded = await owner.post(
        sources_url(org["org_id"]), json=source_body(admin_key=f"  {ANTHROPIC_KEY}\n")
    )
    assert padded.status_code == 201, padded.text
    assert provider_api.requests[0].headers["x-api-key"] == ANTHROPIC_KEY

    too_long = "sk-ant-admin01-" + "a" * 498
    response = await owner.post(
        sources_url(org["org_id"]), json=source_body(name="Long", admin_key=too_long)
    )
    assert response.status_code == 422, response.text
    assert [field for field, _ in field_errors(response)] == ["admin_key"]


async def test_a_provider_that_contradicts_the_kind_is_422(
    owner: Browser, org: dict[str, str], provider_api: ProviderApi
) -> None:
    response = await owner.post(sources_url(org["org_id"]), json=source_body(provider="openai"))
    assert response.status_code == 422, response.text
    assert [field for field, _ in field_errors(response)] == ["provider"]
    assert provider_api.requests == []

    same = await owner.post(sources_url(org["org_id"]), json=source_body(provider="anthropic"))
    assert same.status_code == 201, same.text


async def test_a_csv_source_names_its_provider_and_holds_no_key(
    owner: Browser, org: dict[str, str], provider_api: ProviderApi
) -> None:
    missing = await owner.post(sources_url(org["org_id"]), json=source_body("csv_upload"))
    assert missing.status_code == 422, missing.text
    assert [field for field, _ in field_errors(missing)] == ["provider"]

    with_key = await owner.post(
        sources_url(org["org_id"]),
        json=source_body("csv_upload", provider="google", admin_key=ANTHROPIC_KEY),
    )
    assert with_key.status_code == 422, with_key.text
    assert [field for field, _ in field_errors(with_key)] == ["admin_key"]

    created = await create_source(
        owner, org["org_id"], kind="csv_upload", provider="google", project_ids=[org["p1"]]
    )
    assert (created["provider"], created["status"]) == ("google", "never_fetched")
    assert (created["key_set"], created["key_id"], created["last_fetched_at"]) == (
        False,
        None,
        None,
    )
    assert provider_api.requests == []


async def test_an_api_kind_needs_credentials_keys_and_a_csv_source_does_not(
    settings: Settings,
) -> None:
    api = answering()
    async with running_app(settings, api) as application:
        owner = await signed_up(application, "keyless@example.com")
        try:
            workspace = await create_workspace(owner)
            url = sources_url(workspace.org_id)
            refused = await owner.post(url, json=source_body())
            csv = await owner.post(url, json=source_body("csv_upload", provider="openai"))
        finally:
            await owner.http.aclose()

    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "NOT_CONFIGURED"
    assert refused.json()["detail"] == NEEDS_KEYS
    assert csv.status_code == 201, csv.text
    assert api.requests == []


async def test_a_name_is_unique_in_the_organization(
    owner: Browser, org: dict[str, str], provider_api: ProviderApi
) -> None:
    await create_source(owner, org["org_id"], name="Main")
    response = await owner.post(
        sources_url(org["org_id"]),
        json=source_body("csv_upload", name="Main", provider="openai"),
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "BILLING_SOURCE_NAME_TAKEN"

    other = await create_source(owner, org["org_id"], kind="csv_upload", provider="openai")
    renamed = await owner.patch(
        f"{sources_url(org['org_id'])}/{other['id']}", json={"name": "Main"}
    )
    assert renamed.status_code == 409, renamed.text
    assert renamed.json()["code"] == "BILLING_SOURCE_NAME_TAKEN"


async def test_a_project_of_another_organization_is_422(
    owner: Browser, org: dict[str, str], browser_factory: Any
) -> None:
    stranger: Browser = await browser_factory("stranger@example.com")
    theirs = await create_workspace(stranger, org_name="Theirs")

    response = await owner.post(
        sources_url(org["org_id"]), json=source_body(project_ids=[org["p1"], theirs.project_id])
    )

    assert response.status_code == 422, response.text
    assert [field for field, _ in field_errors(response)] == ["project_ids"]
    assert theirs.project_id not in response.text
    assert (await owner.get(sources_url(org["org_id"]))).json() == []


async def test_a_project_is_compared_with_one_account_per_provider(
    owner: Browser, org: dict[str, str]
) -> None:
    await create_source(owner, org["org_id"], name="Prod account", project_ids=[org["p1"]])

    taken = await owner.post(
        sources_url(org["org_id"]),
        json=source_body(
            "csv_upload",
            name="Invoices",
            provider="anthropic",
            project_ids=[
                org["p2"],
                org["p1"],
            ],
        ),
    )
    assert taken.status_code == 409, taken.text
    problem = taken.json()
    assert problem["code"] == "BILLING_SOURCE_PROJECT_TAKEN"
    assert "Prod account" in problem["detail"]
    assert [error["field"] for error in problem["errors"]] == ["project_ids"]

    openai = await create_source(
        owner, org["org_id"], kind="openai_costs_api", name="OpenAI", project_ids=[org["p1"]]
    )
    assert [project["id"] for project in openai["projects"]] == [org["p1"]]


async def test_patch_replaces_the_projects_and_edits_the_rest(
    owner: Browser,
    org: dict[str, str],
    provider_api: ProviderApi,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    created = await create_source(owner, org["org_id"], project_ids=[org["p1"]])
    url = f"{sources_url(org['org_id'])}/{created['id']}"

    response = await owner.patch(
        url,
        json={
            "name": "Renamed",
            "provider_scope": "wrkspc_01",
            "enabled": False,
            "project_ids": [org["p2"]],
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["name"], body["provider_scope"], body["enabled"]) == (
        "Renamed",
        "wrkspc_01",
        False,
    )
    assert [project["id"] for project in body["projects"]] == [org["p2"]]
    assert body["key_set"] is True
    assert len(provider_api.requests) == 1  # an edit does not check the key again

    cleared = await owner.patch(url, json={"provider_scope": None, "project_ids": []})
    assert cleared.status_code == 200, cleared.text
    assert (cleared.json()["provider_scope"], cleared.json()["projects"]) == (None, [])
    assert cleared.json()["name"] == "Renamed"

    for field in ("name", "enabled", "project_ids"):
        refused = await owner.patch(url, json={field: None})
        assert refused.status_code == 422, (field, refused.text)

    actions = [event.action for event in await audit_rows(session_factory, org["org_id"])]
    assert actions == [
        "billing_source.create",
        "billing_source.update",
        "billing_source.update",
    ]
    # The project p1 is free again: another anthropic source may take it.
    await create_source(
        owner,
        org["org_id"],
        kind="csv_upload",
        name="CSV",
        provider="anthropic",
        project_ids=[org["p1"]],
    )


async def test_patch_a_project_taken_by_another_source_is_409(
    owner: Browser, org: dict[str, str]
) -> None:
    await create_source(owner, org["org_id"], name="First", project_ids=[org["p1"]])
    second = await create_source(owner, org["org_id"], name="Second", project_ids=[org["p2"]])

    response = await owner.patch(
        f"{sources_url(org['org_id'])}/{second['id']}",
        json={"project_ids": [org["p2"], org["p1"]]},
    )

    assert response.status_code == 409, response.text
    assert response.json()["code"] == "BILLING_SOURCE_PROJECT_TAKEN"
    assert "First" in response.json()["detail"]
    # Keeping its own project is no conflict.
    kept = await owner.patch(
        f"{sources_url(org['org_id'])}/{second['id']}", json={"project_ids": [org["p2"]]}
    )
    assert kept.status_code == 200, kept.text


async def test_rotate_a_csv_source_is_422(owner: Browser, org: dict[str, str]) -> None:
    created = await create_source(owner, org["org_id"], kind="csv_upload", provider="openai")
    response = await owner.post(
        f"{sources_url(org['org_id'])}/{created['id']}/rotate",
        json={"admin_key": "sk-admin-Vb3Nq8ZtLx5MwPc1RkYh7GdJs2FeUa9T"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_rotate_checks_the_new_key_and_records_the_outcome(
    owner: Browser,
    org: dict[str, str],
    provider_api: ProviderApi,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    created = await create_source(owner, org["org_id"], project_ids=[org["p1"]])
    url = f"{sources_url(org['org_id'])}/{created['id']}"
    provider_api.default = Reply(status=401, json={"error": {"type": "authentication_error"}})

    response = await owner.post(f"{url}/rotate", json={"admin_key": f" {ANTHROPIC_KEY_2} "})

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["status"], body["last_error"]) == ("failing", "401 Unauthorized")
    assert provider_api.requests[-1].headers["x-api-key"] == ANTHROPIC_KEY_2

    wrong = await owner.post(f"{url}/rotate", json={"admin_key": "sk-admin-0123456789abcdef"})
    assert wrong.status_code == 422, wrong.text
    assert field_errors(wrong) == [("admin_key", NOT_ADMIN_KEY)]

    events = await audit_rows(session_factory, org["org_id"])
    assert [event.action for event in events] == [
        "billing_source.create",
        "billing_source.rotate",
    ]
    assert events[1].metadata_ == {
        "kind": "anthropic_admin_api",
        "provider": "anthropic",
        "project_ids": [org["p1"]],
    }


async def test_delete_removes_the_source(
    owner: Browser, org: dict[str, str], session_factory: async_sessionmaker[AsyncSession]
) -> None:
    created = await create_source(owner, org["org_id"], project_ids=[org["p1"]])
    url = f"{sources_url(org['org_id'])}/{created['id']}"

    response = await owner.delete(url)

    assert response.status_code == 204, response.text
    assert (await owner.get(sources_url(org["org_id"]))).json() == []
    assert (await owner.get(url)).status_code == 404
    assert (await owner.delete(url)).status_code == 404
    async with session_factory() as db:
        mapped = await db.scalar(text("SELECT count(*) FROM billing_source_projects"))
    assert mapped == 0
    events = await audit_rows(session_factory, org["org_id"])
    assert [event.action for event in events][-1] == "billing_source.delete"


async def test_key_write_only(
    owner: Browser,
    org: dict[str, str],
    provider_api: ProviderApi,
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
    app_logs: io.StringIO,
) -> None:
    provider_api.default = Reply(
        status=401, body=f'{{"error": "bad key {ANTHROPIC_KEY_2}"}}'.encode()
    )
    base = sources_url(org["org_id"])
    created = await owner.post(base, json=source_body(project_ids=[org["p1"]]))
    url = f"{base}/{created.json()['id']}"
    responses = [
        created,
        await owner.post(f"{url}/rotate", json={"admin_key": ANTHROPIC_KEY_2}),
        await owner.get(base),
        await owner.get(url),
        await owner.patch(url, json={"name": "Renamed"}),
        await owner.post(base, json=source_body(name="Bad", admin_key=ANTHROPIC_KEY + "\x01")),
        await owner.post(f"{url}/rotate", json={"admin_key": ANTHROPIC_KEY_2 + "é"}),
        await owner.delete(url),
    ]
    assert [response.status_code for response in responses] == [
        201,
        200,
        200,
        200,
        200,
        422,
        422,
        204,
    ]

    async with session_factory() as db:
        audit = await db.scalars(text("SELECT a::text FROM audit_events a"))
        captured = "".join(audit)
    captured += "".join(response.text + str(response.headers) for response in responses)
    captured += app_logs.getvalue() + caplog.text
    captured += "".join(repr(record.msg) + repr(record.args) for record in caplog.records)
    for key in (ANTHROPIC_KEY, ANTHROPIC_KEY_2):
        assert not [window for window in secret_windows(key) if window in captured]
    assert "billing_source_created" in app_logs.getvalue(), "the create is logged"


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (Reply(status=401, json={}), "401 Unauthorized"),
        (Reply(status=403, json={}), "403 Forbidden"),
        (Reply(status=404, json={}), "404 Not Found"),
        (Reply(status=500, body=b"<html>oops</html>"), "500 Internal Server Error"),
        (Reply(body=b"<html>"), "invalid response"),
        (Reply(body=b'{"data": "nope", "has_more": false}'), "invalid response"),
        (Reply(json=[], delay_s=1.0), "timeout"),
        (None, "connection error"),
    ],
    ids=["401", "403", "404", "500", "not-json", "bad-shape", "timeout", "connect"],
)
async def test_last_error_vocabulary(
    owner: Browser,
    org: dict[str, str],
    provider_api: ProviderApi,
    monkeypatch: pytest.MonkeyPatch,
    failure: Reply | None,
    expected: str,
) -> None:
    probe_module = importlib.import_module("app.reconciliation.fetchers.probe")
    monkeypatch.setattr(probe_module, "PROBE_TIMEOUT_S", 0.05)
    provider_api.default = failure
    provider_api.connect_error = failure is None

    created = await create_source(owner, org["org_id"])

    assert (created["status"], created["last_error"]) == ("failing", expected)
    assert created["key_set"] is True  # stored either way: a blip must not lose the key
    assert created["last_error"] in LAST_ERROR_PHRASES or STATUS_LINE.match(created["last_error"])


async def test_a_source_of_billing_sources_disabled_server_is_stored_unchecked(
    gateway_settings: Settings,
) -> None:
    api = answering()
    off = gateway_settings.model_copy(update={"billing_sources_enabled": False})
    async with running_app(off, api) as application:
        owner = await signed_up(application, "airgapped@example.com")
        try:
            workspace = await create_workspace(owner)
            response = await owner.post(sources_url(workspace.org_id), json=source_body())
        finally:
            await owner.http.aclose()

    assert response.status_code == 201, response.text
    assert (response.json()["status"], response.json()["key_set"]) == ("never_fetched", True)
    assert api.requests == []


async def test_key_checks_are_throttled_per_organization(
    owner: Browser, org: dict[str, str], provider_api: ProviderApi, browser_factory: Any
) -> None:
    """Each create or rotate of an API source asks the provider whether the key is live, so an
    unthrottled loop would test stolen admin keys from Spanlight's address. Five checks per
    organization, then one a minute."""
    base = sources_url(org["org_id"])
    created = await create_source(owner, org["org_id"], project_ids=[org["p1"]])
    rotate = f"{base}/{created['id']}/rotate"
    for _ in range(4):
        response = await owner.post(rotate, json={"admin_key": ANTHROPIC_KEY_2})
        assert response.status_code == 200, response.text
    checks = len(provider_api.requests)
    assert checks == 5

    refused = [
        await owner.post(rotate, json={"admin_key": ANTHROPIC_KEY_2}),
        await owner.post(base, json=source_body("openai_costs_api", name="OpenAI")),
    ]

    for response in refused:
        assert response.status_code == 429, response.text
        assert response.json()["code"] == "RATE_LIMITED"
        assert int(response.headers["retry-after"]) >= 1
    assert len(provider_api.requests) == checks
    # A CSV source holds no key, so nothing is checked and nothing is throttled.
    csv = await owner.post(base, json=source_body("csv_upload", name="CSV", provider="openai"))
    assert csv.status_code == 201, csv.text
    # Another organization has its own bucket.
    other: Browser = await browser_factory("other@example.com")
    elsewhere = await create_workspace(other, org_name="Elsewhere")
    assert (await create_source(other, elsewhere.org_id))["status"] == "ok"


async def test_uncheckable_requests_spend_no_key_check_token(
    owner: Browser, org: dict[str, str], provider_api: ProviderApi
) -> None:
    """A rotate of a CSV source holds no key to check, so it must not count against the five
    checks an organization gets: a mistaken loop over it would otherwise lock out real
    rotations."""
    base = sources_url(org["org_id"])
    api = await create_source(owner, org["org_id"], project_ids=[org["p1"]])
    csv = await create_source(
        owner, org["org_id"], kind="csv_upload", name="CSV", provider="openai"
    )
    for _ in range(5):
        refused = await owner.post(
            f"{base}/{csv['id']}/rotate", json={"admin_key": ANTHROPIC_KEY_2}
        )
        assert refused.status_code == 422, refused.text

    for _ in range(4):
        response = await owner.post(
            f"{base}/{api['id']}/rotate", json={"admin_key": ANTHROPIC_KEY_2}
        )
        assert response.status_code == 200, response.text
    assert len(provider_api.requests) == 5


async def test_the_key_check_holds_no_transaction_open(
    owner: Browser,
    org: dict[str, str],
    provider_api: ProviderApi,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The check waits on the provider for up to 10 s; a request idle in a transaction all that
    time pins a pooled connection, so N slow checks would starve every tenant's requests."""
    idle: list[int] = []

    async def count_idle_transactions() -> None:
        async with session_factory() as db:
            idle.append(
                await db.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() AND pid <> pg_backend_pid() "
                        "AND state LIKE 'idle in transaction%'"
                    )
                )
            )

    provider_api.on_request = count_idle_transactions
    created = await create_source(owner, org["org_id"], project_ids=[org["p1"]])
    rotated = await owner.post(
        f"{sources_url(org['org_id'])}/{created['id']}/rotate",
        json={"admin_key": ANTHROPIC_KEY_2},
    )

    assert rotated.status_code == 200, rotated.text
    assert idle == [0, 0]

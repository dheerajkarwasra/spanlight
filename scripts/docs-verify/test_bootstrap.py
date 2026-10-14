"""bootstrap.py must keep what it created, even when a later step fails."""

# ruff: noqa: S101 (asserts are what a test is made of)

import json
import os
import stat
from pathlib import Path

import bootstrap
import httpx
import mock_provider
import pytest
from bootstrap import EnvStore

HOST = "http://api.invalid"
PASSWORD_NAME = "DOCS_VERIFY_PASSWORD"  # noqa: S105 (an env variable name)


class FakeApi:
    """Just enough of the dashboard API to walk bootstrap through every step."""

    def __init__(self) -> None:
        self.password: str | None = None
        self.refuse_credentials = False
        self.objects: dict[str, list[dict]] = {}
        self.secrets: dict[str, str] = {}

    def _create(self, kind: str, body: dict, **extra: object) -> dict:
        item = {"id": f"{kind}-{len(self.objects.get(kind, []))}", **body, **extra}
        self.objects.setdefault(kind, []).append(item)
        return item

    def _listed(self, kind: str) -> list[dict]:
        return self.objects.get(kind, [])

    def _auth(self, path: str, method: str, request: httpx.Request, body: dict) -> httpx.Response:
        if path == "/auth/signup":
            if self.password is not None:
                return httpx.Response(409, json={"code": "EMAIL_TAKEN"})
            self.password = body["password"]
            return httpx.Response(201, json={}, headers={"set-cookie": "spl_csrf=t; Path=/"})
        if path == "/auth/login":
            if body["password"] != self.password:
                return httpx.Response(401, json={"code": "INVALID_CREDENTIALS"})
            return httpx.Response(200, json={"status": "signed_in"})
        if path == "/auth/me":
            if request.headers.get("authorization", "").removeprefix("Bearer ") in self.secrets:
                return httpx.Response(200, json={})
            memberships = [{"org": o, "role": "owner"} for o in self._listed("org")]
            return httpx.Response(200, json={"memberships": memberships})
        token = f"spl_pat_{len(self.secrets)}"
        self.secrets[token] = "pat"
        return httpx.Response(201, json={"token": token})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1")
        body = json.loads(request.content) if request.content else {}
        method = request.method
        if path.startswith("/auth/"):
            return self._auth(path, method, request, body)
        if path == "/orgs" and method == "POST":
            return httpx.Response(201, json=self._create("org", body))
        if path.endswith("/credentials") and method == "POST" and self.refuse_credentials:
            return httpx.Response(409, json={"code": "NOT_CONFIGURED"})
        for suffix, kind in (
            ("/projects", "project"),
            ("/credentials", "credential"),
            ("/gateway/routes", "route"),
            ("/gateway/keys", "gateway_key"),
            ("/keys", "key"),
        ):
            if path.endswith(suffix):
                if method == "GET":
                    return httpx.Response(200, json=self._listed(kind))
                item = self._create(kind, body, prefix=f"{kind}_{len(self._listed(kind))}")
                if kind in ("key", "gateway_key"):
                    item["secret"] = f"{item['prefix']}_secret"
                    item["revoked_at"] = None
                if kind == "route":
                    item["config"] = body["config"]
                return httpx.Response(201, json=item)
        return httpx.Response(404, json={"code": "NOT_FOUND"})


def run(fake: FakeApi, env_file: Path) -> EnvStore:
    store = EnvStore(env_file)
    bootstrap.bootstrap(HOST, store, httpx.MockTransport(fake))
    return store


def test_password_survives_a_failure_after_sign_up(tmp_path: Path) -> None:
    env_file = tmp_path / "docs-verify.env"
    fake = FakeApi()
    fake.refuse_credentials = True

    with pytest.raises(bootstrap.BootstrapError, match="409"):
        run(fake, env_file)

    saved = bootstrap.read_env(env_file)
    assert saved[PASSWORD_NAME] == fake.password
    assert saved["SPANLIGHT_ORG_ID"]
    assert stat.S_IMODE(os.stat(env_file).st_mode) == 0o600

    # The cause is fixed; the rerun signs in with the saved password and finishes.
    fake.refuse_credentials = False
    store = run(fake, env_file)
    assert set(bootstrap.read_env(env_file)) == set(bootstrap.ENV_NAMES)
    assert PASSWORD_NAME not in store.changed
    assert len(fake.objects["org"]) == 1

    # And a third run changes nothing.
    assert run(fake, env_file).changed == []


def test_login_refusal_says_how_to_start_over(tmp_path: Path) -> None:
    env_file = tmp_path / "docs-verify.env"
    env_file.write_text(f"{PASSWORD_NAME}=stale\n")
    with pytest.raises(bootstrap.BootstrapError, match="delete"):
        run(FakeApi(), env_file)


def test_mock_port_and_key_match_the_mock_provider() -> None:
    # bootstrap.py points the stored credential at the mock and gives it the mock's key; a drift
    # between the two files would make every verification run fail at the first provider call.
    assert bootstrap.MOCK_PORT == mock_provider.MOCK_PORT
    assert bootstrap.MOCK_KEY == mock_provider.MOCK_KEY


def test_default_host_is_the_development_api_port() -> None:
    # `uv run uvicorn app.main:app --port 8000` is the documented way to start the API.
    assert bootstrap.DEFAULT_HOST == "http://127.0.0.1:8000"

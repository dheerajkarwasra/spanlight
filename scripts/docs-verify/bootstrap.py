#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27"]
# ///
"""Create the account, project and keys that documentation samples run against.

Everything goes through the product's own HTTP API, never through SQL:

* a user ``docs-verify@example.com`` with a generated password,
* an organization and a project,
* an ingest API key (``ingest:write``),
* a personal access token with the ``write`` scope,
* a gateway credential that points at the mock provider (``mock_provider.py``),
* a route that uses it, and a gateway key on that route.

The values are written to ``.local/docs-verify.env`` (git-ignored, mode 0600). The script
prints the names it wrote and never a value. Each value is saved the moment it exists, so a run
that fails halfway (the account is made, a later step is refused) is resumed by running it again.
A run on a finished setup finds what exists and changes nothing; a missing piece (a revoked key,
say) is created again and only that line changes.

    uv run --script scripts/docs-verify/bootstrap.py [--host http://127.0.0.1:8000]

The API must run with ``GATEWAY_ALLOW_INSECURE_BASE_URLS=true`` (so a credential may point at
``http://127.0.0.1``) and with ``CREDENTIALS_KEYS`` set (so the credential's key can be sealed).
"""

from __future__ import annotations

import argparse
import contextlib
import os
import secrets
import sys
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".local" / "docs-verify.env"

DEFAULT_HOST = "http://127.0.0.1:8000"
# The API only accepts state-changing browser-style requests from its allowed origins.
ORIGIN = "http://localhost:5173"

# Not a `.test` address: the API's email validation refuses reserved top-level domains.
EMAIL = "docs-verify@example.com"
USER_NAME = "Docs Verify"
ORG_NAME = "Docs Verify"
PROJECT_NAME = "docs-verify"
API_KEY_NAME = "docs-verify-ingest"
TOKEN_NAME = "docs-verify"  # noqa: S105 (the name of a token, not a secret)
CREDENTIAL_NAME = "docs-verify-mock"
ROUTE_NAME = "docs-verify"
GATEWAY_KEY_NAME = "docs-verify"

# These two values are shared with mock_provider.py.
MOCK_PORT = 9900
MOCK_KEY = "docs-verify-key"
MOCK_BASE_URL = f"http://127.0.0.1:{MOCK_PORT}"

ENV_NAMES = (
    "SPANLIGHT_HOST",
    "SPANLIGHT_API_KEY",
    "SPANLIGHT_PAT",
    "SPANLIGHT_PROJECT_ID",
    "SPANLIGHT_ORG_ID",
    "SPANLIGHT_GATEWAY_KEY",
    "SPANLIGHT_CREDENTIAL_ID",
    "DOCS_VERIFY_PASSWORD",
)


class BootstrapError(RuntimeError):
    pass


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            name, _, value = line.partition("=")
            values[name.strip()] = value.strip()
    return values


def write_env(path: Path, values: dict[str, str]) -> None:
    """Write the values we have, in the canonical order, atomically and with mode 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{name}={values[name]}\n" for name in ENV_NAMES if name in values)
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(body)
    os.replace(temporary, path)


class EnvStore:
    """The env file, saved as each value is obtained.

    A step that fails halfway must not lose what earlier steps created: above all the generated
    password, without which a rerun cannot sign in to the account that was just made.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.values = read_env(path)
        self.changed: list[str] = []

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> str:
        if self.values.get(name) != value:
            self.values[name] = value
            self.changed.append(name)
            write_env(self.path, self.values)
        return value


class Api:
    """The dashboard API as a signed-in browser session, with the CSRF header on writes."""

    def __init__(self, host: str, transport: httpx.BaseTransport | None = None) -> None:
        self.host = host
        self.transport = transport
        self.http = httpx.Client(
            base_url=host, timeout=30, headers={"Origin": ORIGIN}, transport=transport
        )

    def bearer_status(self, path: str, token: str) -> int:
        """The status of a GET made with a token alone, without this session's cookies."""
        with httpx.Client(base_url=self.host, timeout=30, transport=self.transport) as client:
            response = client.get(f"/api/v1{path}", headers={"Authorization": f"Bearer {token}"})
        return response.status_code

    def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}))
        if method not in ("GET", "HEAD"):
            token = self.http.cookies.get("spl_csrf")
            if token:
                headers["X-CSRF-Token"] = token
        return self.http.request(method, f"/api/v1{path}", headers=headers, **kwargs)

    def expect(self, response: httpx.Response, *statuses: int) -> Any:
        if response.status_code not in statuses:
            code = ""
            with contextlib.suppress(ValueError):
                code = str(response.json().get("code", ""))
            raise BootstrapError(
                f"{response.request.method} {response.request.url.path} answered "
                f"{response.status_code} {code}".strip()
            )
        return response.json() if response.content else None


def sign_in(api: Api, password: str, *, known_password: bool) -> None:
    """Log in with the stored password, or sign up the first time."""
    credentials = {"email": EMAIL, "password": password}
    if known_password:
        response = api.request("POST", "/auth/login", json=credentials)
        if response.status_code == 401:
            raise BootstrapError(
                f"login as {EMAIL} was refused. If the database was reset, delete "
                f"{ENV_FILE.name} and run this again."
            )
        body = api.expect(response, 200)
        if body.get("status") != "signed_in":
            raise BootstrapError("the account asks for a second step; use an account without 2FA")
        return
    response = api.request("POST", "/auth/signup", json={**credentials, "name": USER_NAME})
    if response.status_code == 409:
        raise BootstrapError(
            f"{EMAIL} already exists but {ENV_FILE.name} has no password for it. "
            "Delete the account in the dashboard (Settings) and run this again."
        )
    api.expect(response, 201)
    # Signing up also signs in; the CSRF cookie is now set.


def ensure_org(api: Api) -> str:
    me = api.expect(api.request("GET", "/auth/me"), 200)
    for membership in me["memberships"]:
        if membership["org"]["name"] == ORG_NAME and membership["role"] == "owner":
            return membership["org"]["id"]
    return api.expect(api.request("POST", "/orgs", json={"name": ORG_NAME}), 201)["id"]


def ensure_project(api: Api, org_id: str) -> str:
    for project in api.expect(api.request("GET", f"/orgs/{org_id}/projects"), 200):
        if project["name"] == PROJECT_NAME:
            return project["id"]
    created = api.request("POST", f"/orgs/{org_id}/projects", json={"name": PROJECT_NAME})
    return api.expect(created, 201)["id"]


def key_is_listed(listed: list[dict[str, Any]], name: str, secret: str) -> bool:
    """Whether ``secret`` belongs to a live key called ``name`` (a listing shows prefixes only)."""
    return any(
        entry["name"] == name
        and entry.get("revoked_at") is None
        and secret.startswith(entry["prefix"] + "_")
        for entry in listed
    )


def ensure_api_key(api: Api, project_id: str, current: str | None) -> str:
    listed = api.expect(api.request("GET", f"/projects/{project_id}/keys"), 200)
    if current and key_is_listed(listed, API_KEY_NAME, current):
        return current
    body = {"name": API_KEY_NAME, "scopes": ["ingest:write"]}
    created = api.request("POST", f"/projects/{project_id}/keys", json=body)
    return api.expect(created, 201)["secret"]


def ensure_pat(api: Api, current: str | None) -> str:
    if current and api.bearer_status("/auth/me", current) == 200:
        return current
    body = {"name": TOKEN_NAME, "scope": "write"}
    return api.expect(api.request("POST", "/auth/tokens", json=body), 201)["token"]


def ensure_credential(api: Api, org_id: str) -> str:
    for credential in api.expect(api.request("GET", f"/orgs/{org_id}/credentials"), 200):
        if credential["name"] == CREDENTIAL_NAME and credential["base_url"] == MOCK_BASE_URL:
            return credential["id"]
    body = {
        "name": CREDENTIAL_NAME,
        "provider": "openai_compatible",
        "api_key": MOCK_KEY,
        "base_url": MOCK_BASE_URL,
    }
    return api.expect(api.request("POST", f"/orgs/{org_id}/credentials", json=body), 201)["id"]


def ensure_route(api: Api, project_id: str, credential_id: str) -> str:
    base = f"/projects/{project_id}/gateway/routes"
    for route in api.expect(api.request("GET", base), 200):
        targets = route["config"]["targets"]
        if route["name"] == ROUTE_NAME and any(
            t["credential_id"] == credential_id for t in targets
        ):
            return route["id"]
    config = {"targets": [{"credential_id": credential_id}], "retry": {}, "fallback": {}}
    return api.expect(api.request("POST", base, json={"name": ROUTE_NAME, "config": config}), 201)[
        "id"
    ]


def ensure_gateway_key(api: Api, project_id: str, route_id: str, current: str | None) -> str:
    base = f"/projects/{project_id}/gateway/keys"
    listed = api.expect(api.request("GET", base), 200)
    if current and key_is_listed(listed, GATEWAY_KEY_NAME, current):
        return current
    body = {"name": GATEWAY_KEY_NAME, "route_id": route_id, "environment": "development"}
    return api.expect(api.request("POST", base, json=body), 201)["secret"]


def bootstrap(host: str, store: EnvStore, transport: httpx.BaseTransport | None = None) -> None:
    """Make every object exist, saving each value to ``store`` the moment it is known."""
    api = Api(host, transport)
    known = store.get("DOCS_VERIFY_PASSWORD")
    password = known or secrets.token_urlsafe(24)
    sign_in(api, password, known_password=known is not None)
    # Signed up: from here on the account exists, so its password must survive any failure.
    store.set("DOCS_VERIFY_PASSWORD", password)
    store.set("SPANLIGHT_HOST", host)
    org_id = store.set("SPANLIGHT_ORG_ID", ensure_org(api))
    project_id = store.set("SPANLIGHT_PROJECT_ID", ensure_project(api, org_id))
    credential_id = store.set("SPANLIGHT_CREDENTIAL_ID", ensure_credential(api, org_id))
    route_id = ensure_route(api, project_id, credential_id)
    store.set("SPANLIGHT_API_KEY", ensure_api_key(api, project_id, store.get("SPANLIGHT_API_KEY")))
    store.set("SPANLIGHT_PAT", ensure_pat(api, store.get("SPANLIGHT_PAT")))
    store.set(
        "SPANLIGHT_GATEWAY_KEY",
        ensure_gateway_key(api, project_id, route_id, store.get("SPANLIGHT_GATEWAY_KEY")),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default=os.environ.get("SPANLIGHT_HOST", DEFAULT_HOST))
    args = parser.parse_args()
    host = args.host.rstrip("/")
    store = EnvStore(ENV_FILE)
    try:
        bootstrap(host, store)
    except (BootstrapError, httpx.HTTPError) as error:
        print(f"bootstrap failed: {error}", file=sys.stderr)
        if store.changed:
            print(f"saved before the failure: {', '.join(sorted(store.changed))}", file=sys.stderr)
        return 1
    name = ENV_FILE.relative_to(REPO_ROOT)
    if store.changed:
        print(f"wrote {name}: {', '.join(sorted(store.changed))}")
    else:
        print(f"{name} is up to date: nothing changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

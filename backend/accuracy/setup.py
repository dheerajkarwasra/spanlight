"""Pilot account, organization, Anthropic credential and personal access token on the local stack.

Everything goes through the product's HTTP API (session cookie, CSRF, Origin). The ids, the PAT
and the generated password are written only to the state file (`accuracy.paths`, mode 0600).
The Anthropic key is read from `ANTHROPIC_API_KEY` and sent only to the local API, which seals
it; nothing here prints a secret.

    cd backend && uv run python -m accuracy.setup     # idempotent: reruns create what is missing
"""

import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

from accuracy.dashboard import Api, SetupError, key_is_listed
from accuracy.paths import MissingKeyError, anthropic_api_key, harness_paths

HOST = os.environ.get("SPANLIGHT_HOST", "http://127.0.0.1:8000")
EMAIL = "pilot@example.com"
ORG_NAME = "Pilot"
CREDENTIAL_NAME = "anthropic-pilot"


class State:
    """A JSON state file, saved atomically with mode 0600 on every `set`."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(self.data, fh, indent=1)
        os.replace(tmp, self.path)

    def __getitem__(self, k: str) -> Any:
        return self.data[k]

    def get(self, k: str, default: Any = None) -> Any:
        return self.data.get(k, default)

    def set(self, k: str, v: Any) -> Any:
        self.data[k] = v
        self.save()
        return v


def pilot_state() -> State:
    return State(harness_paths().state)


def session(state: State) -> Api:
    """A signed-in session for the pilot account; signs up (and stores the password) once."""
    api = Api(HOST)
    password = state.get("password")
    creds = {"email": EMAIL, "password": password}
    if password:
        body = api.expect(api.request("POST", "/auth/login", json=creds), 200)
        if body.get("status") != "signed_in":
            raise SetupError("pilot account asks for a second step")
        return api
    password = secrets.token_urlsafe(24)
    resp = api.request(
        "POST", "/auth/signup", json={"email": EMAIL, "password": password, "name": "Pilot"}
    )
    api.expect(resp, 201)
    state.set("password", password)
    return api


def _ensure_org(api: Api) -> str:
    me = api.expect(api.request("GET", "/auth/me"), 200)
    for membership in me["memberships"]:
        if membership["org"]["name"] == ORG_NAME:
            return str(membership["org"]["id"])
    return str(api.expect(api.request("POST", "/orgs", json={"name": ORG_NAME}), 201)["id"])


def ensure_base(api: Api, state: State) -> None:
    org = state.set("org_id", _ensure_org(api))
    creds = api.expect(api.request("GET", f"/orgs/{org}/credentials"), 200)
    cred = next((c["id"] for c in creds if c["name"] == CREDENTIAL_NAME), None)
    if cred is None:
        body = {"name": CREDENTIAL_NAME, "provider": "anthropic", "api_key": anthropic_api_key()}
        cred = api.expect(api.request("POST", f"/orgs/{org}/credentials", json=body), 201)["id"]
    state.set("credential_id", cred)

    pat = state.get("pat")
    if not pat or api.bearer_status("/auth/me", pat) != 200:
        body = {"name": "pilot", "scope": "write"}
        state.set("pat", api.expect(api.request("POST", "/auth/tokens", json=body), 201)["token"])


def ensure_project(api: Api, state: Any, name: str) -> dict[str, str]:
    """A project with an ingest key, recorded under `state["projects"][name]`.

    `state` is a `State` or a view with the same `__getitem__`/`get`/`set` (the Doctor harness
    passes one scoped to a second organization).
    """
    org = state["org_id"]
    projects = state.get("projects", {})
    entry: dict[str, str] = projects.get(name, {})
    listed = api.expect(api.request("GET", f"/orgs/{org}/projects"), 200)
    pid = next((p["id"] for p in listed if p["name"] == name), None)
    if pid is None:
        created = api.request("POST", f"/orgs/{org}/projects", json={"name": name})
        pid = api.expect(created, 201)["id"]
    entry["project_id"] = pid
    keys = api.expect(api.request("GET", f"/projects/{pid}/keys"), 200)
    if not (entry.get("ingest_key") and key_is_listed(keys, "pilot-ingest", entry["ingest_key"])):
        body = {"name": "pilot-ingest", "scopes": ["ingest:write"]}
        entry["ingest_key"] = api.expect(
            api.request("POST", f"/projects/{pid}/keys", json=body), 201
        )["secret"]
    projects[name] = entry
    state.set("projects", projects)
    return entry


def main() -> int:
    state = pilot_state()
    try:
        api = session(state)
        ensure_base(api, state)
    except (SetupError, MissingKeyError) as error:
        print(f"setup failed: {error}", file=sys.stderr)
        return 1
    print(f"ok: org {state['org_id']}, credential {state['credential_id']}, PAT stored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

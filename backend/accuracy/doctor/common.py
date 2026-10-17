"""Shared pieces of the Doctor accuracy harness: constants, ids, state and the HTTP reads/writes.

State: the pilot's state file (`accuracy.paths`) is read, never written here; the harness keeps
its own projects, keys and run records in the Doctor state file beside it. Nothing here prints a
secret. The Anthropic clients and the ledger-guarded call helpers are in `clients`, the synthetic
span builders in `spans`, the per-scenario run context in `context`.
"""

import secrets
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from accuracy.ledger import Ledger
from accuracy.paths import harness_paths
from accuracy.setup import HOST, State

HAIKU = "claude-haiku-5-5"
HAIKU45 = "claude-haiku-4-5"
PROVIDER = "anthropic"
INCIDENT_ORG_NAME = "Pilot-incident"

SESSION_HEADER = "x-spanlight-session"
TRACE_ID_HEADER = "x-spanlight-trace-id"
TAGS_HEADER = "x-spanlight-tags"
FAULT_HEADER = "x-spanlight-fault"


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def new_trace_id() -> str:
    return secrets.token_hex(16)


def new_span_id() -> str:
    return secrets.token_hex(8)


def spend_ledger() -> Ledger:
    """The spend ledger at the configured path (read from the environment at call time)."""
    return Ledger()


# --------------------------------------------------------------------------- state


def doctor_state() -> State:
    return State(harness_paths().doctor_state)


def main_state() -> State:
    """The pilot's own state (org, credential, PAT, password); read, never written here."""
    return State(harness_paths().state)


class OrgScopedState:
    """A view of the doctor state for one organization.

    `setup.ensure_project` reads `org_id` and keeps its map under `projects`; this view points
    both at the keys of one organization, so the same helper serves the pilot org and the
    incident orgs without either overwriting the other's projects.
    """

    def __init__(self, state: State, org_key: str, projects_key: str) -> None:
        self.state = state
        self.org_key = org_key
        self.projects_key = projects_key

    def _map(self, key: str) -> str:
        if key == "org_id":
            return self.org_key
        if key == "projects":
            return self.projects_key
        return key

    def __getitem__(self, key: str) -> Any:
        return self.state[self._map(key)]

    def get(self, key: str, default: Any = None) -> Any:
        return self.state.get(self._map(key), default)

    def set(self, key: str, value: Any) -> Any:
        return self.state.set(self._map(key), value)


def all_projects(state: State) -> dict[str, dict[str, Any]]:
    """Every project the harness set up: the pilot org's and each incident org's."""
    projects: dict[str, dict[str, Any]] = {}
    for key, value in state.data.items():
        if (key == "projects" or key.endswith("_projects")) and isinstance(value, dict):
            projects.update(value)
    return projects


# --------------------------------------------------------------------------- dashboard reads


class PatApi:
    """Dashboard reads with the pilot's personal access token (no cookies, no CSRF)."""

    def __init__(self, pat: str, host: str = HOST) -> None:
        self.http = httpx.Client(
            base_url=f"{host}/api/v1", timeout=30, headers={"Authorization": f"Bearer {pat}"}
        )

    def get(self, path: str, **params: Any) -> Any:
        # The API rate-limits each credential; wait out a 429 as its Retry-After asks.
        for _ in range(8):
            response = self.http.get(path, params=params)
            if response.status_code != 429:
                break
            time.sleep(min(float(response.headers.get("retry-after") or 2), 30))
        if response.status_code != 200:
            raise RuntimeError(f"GET {path} answered {response.status_code}: {response.text[:200]}")
        return response.json()

    def insights(
        self, project_id: str, statuses: Sequence[str] = ("open",)
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"status": list(statuses), "limit": 100}
            if cursor:
                params["cursor"] = cursor
            page = self.get(f"/projects/{project_id}/insights", **params)
            rows.extend(page.get("items", page.get("data", [])))
            cursor = page.get("next_cursor")
            if not cursor:
                return rows

    def detector_runs(self, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = self.get(f"/projects/{project_id}/detector-runs", limit=limit)
        return runs

    def trace(self, project_id: str, trace_id: str) -> dict[str, Any]:
        detail: dict[str, Any] = self.get(f"/projects/{project_id}/traces/{trace_id}")
        return detail


# --------------------------------------------------------------------------- ingest


INGEST_BATCH = 500


def ingest(ingest_key: str, spans: Sequence[dict[str, Any]], host: str = HOST) -> int:
    """POST the spans in batches of `INGEST_BATCH`; raise when any span is rejected."""
    accepted = 0
    with httpx.Client(
        base_url=host, timeout=60, headers={"Authorization": f"Bearer {ingest_key}"}
    ) as client:
        for start in range(0, len(spans), INGEST_BATCH):
            batch = spans[start : start + INGEST_BATCH]
            response = client.post("/v1/traces", json={"spans": list(batch)})
            if response.status_code not in (200, 202):
                raise RuntimeError(
                    f"POST /v1/traces answered {response.status_code}: {response.text[:300]}"
                )
            body = response.json()
            if body.get("rejected"):
                raise RuntimeError(f"ingest rejected spans: {body['rejected'][:3]}")
            accepted += int(body.get("accepted", 0))
            time.sleep(0.05)  # stay well under the per-key 50 req/s bucket
    return accepted

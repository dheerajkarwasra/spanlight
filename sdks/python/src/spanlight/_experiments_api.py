"""The slice of the dashboard API (``/api/v1``) the experiment runner reads and writes.

Authenticated with a personal access token. Every failure becomes an :class:`ExperimentError`
whose message names the request and the server's problem ``code`` and ``detail`` and never the
token or a response body that is not a problem document.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from ._experiments_parse import PromptRef
from ._version import __version__

_PAGE_SIZE = 200
# A guard against a server that keeps returning cursors: 50 pages of 200 is 10 000 rows.
_MAX_PAGES = 50
_ESCAPE = "~"
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class ExperimentError(Exception):
    """The run could not be set up or finished; the message is safe to print."""


@dataclass(frozen=True)
class ResolvedPrompt:
    """One prompt version the experiment will run."""

    name: str
    version: int
    version_id: str


def _require_safe_host(host: str) -> None:
    """Refuse to send the access token over plain http, except to the local machine."""
    parts = urlsplit(host)
    if parts.scheme == "https" and parts.hostname:
        return
    if parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS:
        return
    raise ExperimentError(
        "the host must be an https:// URL (http:// is allowed only for localhost); "
        "the access token is never sent in clear text"
    )


def wire_name(name: str) -> str:
    """A prompt name as it travels in a path: the server strips exactly one leading ``~``."""
    escaped = f"{_ESCAPE}{name}" if name in {".", ".."} or name.startswith(_ESCAPE) else name
    return quote(escaped, safe="/")


class Api:
    """A small JSON client for one Spanlight host."""

    def __init__(
        self,
        *,
        host: str,
        token: str,
        project_id: str,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        _require_safe_host(host)
        self.project_id = project_id
        self._client = httpx.Client(
            base_url=f"{host.rstrip('/')}/api/v1",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": f"spanlight-python/{__version__} experiments",
            },
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        """Release the connection pool."""
        self._client.close()

    def project(self, path: str = "") -> str:
        """A path under this project."""
        return f"/projects/{self.project_id}{path}"

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        """Send one request and return the decoded JSON body."""
        try:
            response = self._client.request(method, path, params=params, headers=headers, json=body)
        except httpx.HTTPError as error:
            raise ExperimentError(
                f"{method} {path}: cannot reach the server ({type(error).__name__})"
            ) from None
        if response.is_success:
            try:
                return response.json()
            except ValueError:
                raise ExperimentError(f"{method} {path}: the answer is not JSON") from None
        raise ExperimentError(f"{method} {path}: {_problem(response)}")

    def pages(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict[str, Any]]:
        """Every item of a keyset-paginated list, newest first."""
        cursor: str | None = None
        for _ in range(_MAX_PAGES):
            query: dict[str, Any] = {**(params or {}), "limit": _PAGE_SIZE}
            if cursor:
                query["cursor"] = cursor
            page = self.request("GET", path, params=query)
            yield from page["items"]
            cursor = page.get("next_cursor")
            if not cursor:
                return


def _problem(response: httpx.Response) -> str:
    """``401 UNAUTHENTICATED: detail`` from a problem document, else just the status."""
    try:
        body = response.json()
        code, detail = body.get("code"), body.get("detail")
    except (ValueError, AttributeError):
        return str(response.status_code)
    parts = [str(response.status_code)]
    if isinstance(code, str):
        parts.append(code)
    text = " ".join(parts)
    return f"{text}: {detail}" if isinstance(detail, str) and detail else text


def find_by_name(api: Api, path: str, name: str) -> dict[str, Any] | None:
    """The listed item called ``name``, or ``None``."""
    return next((item for item in api.pages(path) if item.get("name") == name), None)


def resolve_dataset(api: Api, name: str) -> dict[str, Any]:
    """The dataset called ``name`` (the server filters by exact name)."""
    for item in api.pages(api.project("/datasets"), {"name": name}):
        if item.get("name") == name:
            return item
    raise ExperimentError(f"dataset {name!r} not found in the project")


def resolve_prompt(api: Api, ref: PromptRef) -> ResolvedPrompt:
    """Pin a prompt reference to one version: its label (``production`` by default) or number."""
    base = api.project(f"/prompts/{wire_name(ref.name)}")
    version = ref.version
    if version is None:
        label = ref.label or "production"
        prompt = api.request("GET", base)
        if label == "latest":
            version = int(prompt["latest"]["version"])
        else:
            labels = {entry["label"]: int(entry["version"]) for entry in prompt["labels"]}
            if label not in labels:
                raise ExperimentError(f"prompt {ref.name!r} has no label {label!r}")
            version = labels[label]
    found = api.request("GET", f"{base}/versions/{version}")
    return ResolvedPrompt(name=ref.name, version=version, version_id=found["id"])


def resolve_evaluators(api: Api, names: list[str]) -> list[str]:
    """Evaluator ids for ``names``, in order; an unknown name is an error."""
    if not names:
        return []
    by_name = {item["name"]: item["id"] for item in api.pages(api.project("/evaluators"))}
    missing = [name for name in names if name not in by_name]
    if missing:
        raise ExperimentError(f"evaluator not found in the project: {', '.join(missing)}")
    return [by_name[name] for name in names]

"""The dashboard API as a signed-in browser session, for the harness's setup steps.

The same pattern as `scripts/docs-verify/bootstrap.py`: a session cookie, the CSRF header on
writes, and an `Origin` the API allows. Kept here rather than imported across the tree, so the
harness runs as a package of the backend.
"""

import contextlib
from typing import Any

import httpx

# The API only accepts state-changing browser-style requests from its allowed origins.
ORIGIN = "http://localhost:5173"


class SetupError(RuntimeError):
    """A setup step the API refused; the message names the request and status, never a body."""


class Api:
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
            raise SetupError(
                f"{response.request.method} {response.request.url.path} answered "
                f"{response.status_code} {code}".strip()
            )
        return response.json() if response.content else None


def key_is_listed(listed: list[dict[str, Any]], name: str, secret: str) -> bool:
    """Whether `secret` belongs to a live key called `name` (a listing shows prefixes only)."""
    return any(
        entry["name"] == name
        and entry.get("revoked_at") is None
        and secret.startswith(entry["prefix"] + "_")
        for entry in listed
    )

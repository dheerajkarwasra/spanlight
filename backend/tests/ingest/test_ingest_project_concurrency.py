"""A project's slow body cannot consume every tenant's ingest capacity."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from tests.helpers import Workspace, bearer, new_trace_id, span
from tests.ingest.support import (
    another_project,
    ingest_native,
    otlp_json_document,
    otlp_json_span,
    workspace_for_project,
)


async def test_project_concurrency_refuses_before_body_read_and_releases_after_completion(
    app: Any,
    client: httpx.AsyncClient,
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        app.state,
        "settings",
        app.state.settings.model_copy(update={"ingest_max_in_flight_per_project": 1}),
    )
    other = await another_project(workspace, "Other project")
    same_project = await workspace_for_project(
        workspace.owner, workspace.org_id, workspace.project_id
    )
    reading = asyncio.Event()
    release = asyncio.Event()
    body = json.dumps({"spans": [span(trace_id=new_trace_id())]}).encode()

    async def held_body() -> AsyncIterator[bytes]:
        reading.set()
        await release.wait()
        yield body

    async def unread_body() -> AsyncIterator[bytes]:
        pytest.fail("A refused request must not read its body")
        yield b""

    async with asyncio.timeout(10):
        held = asyncio.create_task(
            client.post(
                "/v1/traces",
                content=held_body(),
                headers={**bearer(workspace.api_key), "Content-Type": "application/json"},
            )
        )
        try:
            await reading.wait()
            refused = await client.post(
                "/v1/traces",
                content=unread_body(),
                headers={**bearer(same_project.api_key), "Content-Type": "application/json"},
            )
            assert refused.status_code == 429
            assert refused.json()["code"] == "RATE_LIMITED"
            assert refused.json()["detail"] == (
                "Too many concurrent ingest requests for this project."
            )
            assert refused.headers["Retry-After"] == "1"

            refused_otlp = await client.post(
                "/v1/otlp/traces",
                content=unread_body(),
                headers={**bearer(same_project.api_key), "Content-Type": "application/json"},
            )
            assert refused_otlp.status_code == 429
            assert refused_otlp.json()["code"] == "RATE_LIMITED"
            assert refused_otlp.headers["Retry-After"] == "1"

            admitted = await ingest_native(client, other, [span(trace_id=new_trace_id())])
            assert admitted.status_code == 200
            assert admitted.json() == {"accepted": 1, "rejected": []}
            assert not held.done()
        finally:
            release.set()
            completed = await held

        assert completed.status_code == 200
        assert completed.json() == {"accepted": 1, "rejected": []}
        again = await ingest_native(client, workspace, [span(trace_id=new_trace_id())])
        assert again.status_code == 200
        assert again.json() == {"accepted": 1, "rejected": []}
        again_otlp = await client.post(
            "/v1/otlp/traces",
            json=otlp_json_document(otlp_json_span()),
            headers=bearer(workspace.api_key),
        )
        assert again_otlp.status_code == 200
        assert again_otlp.json() == {}

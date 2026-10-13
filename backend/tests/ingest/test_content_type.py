"""`POST /v1/traces` reads any body that parses as JSON, whatever its Content-Type says.

Owner decision 2026-10-13 (full test pass finding T6-03): native ingestion keeps accepting
`text/plain` or a missing Content-Type when the body is JSON, so a client that labels its body
loosely (curl `-d`, a shell script, a minimal HTTP library) keeps working. The route needs a
bearer API key, so a cross-site form post cannot use it. OTLP stays strict: it serves two
encodings and must know which one it was sent.
"""

import json

import httpx
import pytest

from tests.helpers import Workspace, bearer, new_trace_id, span


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", None])
async def test_native_ingestion_reads_json_whatever_the_content_type(
    client: httpx.AsyncClient, workspace: Workspace, content_type: str | None
) -> None:
    headers = bearer(workspace.api_key)
    if content_type is not None:
        headers["Content-Type"] = content_type
    body = json.dumps({"spans": [span(trace_id=new_trace_id())]}).encode()

    response = await client.post("/v1/traces", content=body, headers=headers)

    assert response.status_code == 200
    assert response.json() == {"accepted": 1, "rejected": []}


async def test_otlp_still_refuses_a_body_that_is_not_json_or_protobuf_by_type(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    response = await client.post(
        "/v1/otlp/traces",
        content=b'{"resourceSpans": []}',
        headers={**bearer(workspace.api_key), "Content-Type": "text/plain"},
    )

    assert response.status_code == 415
    assert response.json()["code"] == "UNSUPPORTED_MEDIA_TYPE"

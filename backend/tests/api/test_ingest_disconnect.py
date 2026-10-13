"""A client that leaves mid-body on an ingestion route is its own failure, not a server error."""

from typing import Any

import pytest

from tests.gateway_support import logged
from tests.half_sent_body import response_body, response_status, send_half_and_leave
from tests.helpers import Workspace, bearer, create_api_key, create_workspace


@pytest.fixture
async def workspace(browser_factory: Any) -> Workspace:
    return await create_workspace(await browser_factory("owner@example.com"))


@pytest.mark.parametrize("path", ["/v1/traces", "/v1/otlp/traces", "/v1/scores"])
async def test_a_client_disconnect_mid_body_is_a_400_and_logs_no_unhandled_exception(
    app: Any, workspace: Workspace, caplog: pytest.LogCaptureFixture, path: str
) -> None:
    api_key = workspace.api_key
    if path == "/v1/scores":
        scores_key = await create_api_key(
            workspace.owner, workspace.project_id, scopes=["scores:write"]
        )
        api_key = scores_key["secret"]
    headers = [*bearer(api_key).items(), ("Content-Type", "application/json")]

    sent = await send_half_and_leave(app, path, headers, b'{"spans":[')

    assert logged(caplog, "unhandled_exception") == []
    assert response_status(sent) == 400
    body = response_body(sent)
    assert b'"code":"BAD_REQUEST"' in body
    assert b"request body was not fully sent" in body

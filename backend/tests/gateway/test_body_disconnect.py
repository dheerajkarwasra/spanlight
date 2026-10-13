"""A gateway client that leaves while sending its request body is answered `400 INVALID_REQUEST`.

Nobody reads the answer; what matters is that a dropped upload is the client's failure, not an
`unhandled_exception` logged as a server error, and that no provider is called.
"""

from typing import Any

import pytest

from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import FakeProvider, logged
from tests.half_sent_body import response_body, response_status, send_half_and_leave


@pytest.mark.parametrize("path", ["/gw/v1/chat/completions", "/gw/v1/messages"])
async def test_a_client_disconnect_mid_body_is_a_400_and_logs_no_unhandled_exception(
    gateway_app: Any,
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    caplog: pytest.LogCaptureFixture,
    path: str,
) -> None:
    headers = [
        ("Authorization", f"Bearer {gateway_workspace.gateway_key}"),
        ("Content-Type", "application/json"),
    ]

    sent = await send_half_and_leave(gateway_app, path, headers, b'{"model":"gpt-4o",')

    assert logged(caplog, "unhandled_exception") == []
    assert response_status(sent) == 400
    assert b"not fully sent" in response_body(sent)
    assert fake_provider.requests == []

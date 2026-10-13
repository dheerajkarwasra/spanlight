"""A playground answer links to its trace only when the call's span was stored."""

from typing import Any

import pytest

from app.gateway import call
from tests.evals.experiments import MODEL, exact_usage, reply
from tests.evals.runs import Sessions
from tests.gateway_fixtures import GatewayWorkspace
from tests.gateway_support import FakeProvider, read_gateway_spans


async def test_playground_answer_has_no_trace_link_when_its_span_cannot_be_built(
    gateway_workspace: GatewayWorkspace,
    fake_provider: FakeProvider,
    session_factory: Sessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The provider answered (and may have billed), but the gateway could not build the call's
    # span, so nothing was stored. The answer still comes back; its trace and span ids are null
    # rather than pointing at a trace that does not exist.
    reply(fake_provider, exact_usage())

    def no_span(*_: Any, **__: Any) -> dict[str, Any]:
        raise ValueError("span could not be built")

    monkeypatch.setattr(call, "build_span", no_span)

    response = await gateway_workspace.owner.post(
        f"/api/v1/projects/{gateway_workspace.project_id}/playground/runs",
        json={
            "configs": [
                {
                    "credential_id": str(gateway_workspace.openai_credential_id),
                    "model": MODEL,
                    "params": {"max_tokens": 16},
                    "source": {"messages": [{"role": "user", "content": "Hello"}]},
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    (result,) = response.json()["results"]
    assert result["error"] is None and result["output"] == "ok"
    assert result["trace_id"] is None and result["span_id"] is None
    assert len(fake_provider.requests) == 1
    assert await read_gateway_spans(session_factory, gateway_workspace.project_id) == []

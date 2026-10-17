"""The trace detail exposes `usage_state` and `usage_rollup` on every span.

Every span is stored by the real pipeline (`POST /v1/traces`), so the state is read from the
columns ingestion wrote. Failure modes pinned here: a priced span not reported as `priced`; an
`llm` span that sent no usage shown without a reason; a `tool` span given an `llm` state; the
rollup flag missing from the read model.
"""

from typing import Any

import httpx
import pytest

from tests.helpers import Browser, Workspace, create_workspace, new_span_id, new_trace_id, span
from tests.ingest.support import ingest_native


@pytest.fixture
async def workspace(browser_factory: Any) -> Workspace:
    owner: Browser = await browser_factory("owner@example.com")
    return await create_workspace(owner)


async def test_trace_detail_exposes_usage_state_and_rollup(
    client: httpx.AsyncClient, workspace: Workspace
) -> None:
    trace_id = new_trace_id()
    priced = span(trace_id=trace_id, span_id=new_span_id(), name="priced")
    unreported = span(trace_id=trace_id, span_id=new_span_id(), name="unreported", usage=None)
    tool = span(
        trace_id=trace_id, span_id=new_span_id(), name="tool", kind="tool", model=None, usage=None
    )

    response = await ingest_native(client, workspace, [priced, unreported, tool])
    assert response.status_code == 200, response.text

    shown = await workspace.owner.get(f"/api/v1/projects/{workspace.project_id}/traces/{trace_id}")
    assert shown.status_code == 200, shown.text
    states = {
        item["name"]: (item["usage_state"], item["usage_rollup"]) for item in shown.json()["spans"]
    }
    assert states == {
        "priced": ("priced", False),
        "unreported": ("usage_not_reported", False),
        "tool": ("not_applicable", False),
    }

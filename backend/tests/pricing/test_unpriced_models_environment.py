"""The unpriced-models list takes the `environment` filter (UX Phase 4 spec §7).

Failure mode: the Pricing page, filtered to one environment, lists models only another
environment called.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from tests.helpers import bearer, create_workspace, new_trace_id, span


async def test_unpriced_models_filter_by_environment(
    browser_factory: Any, client: httpx.AsyncClient
) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    start = datetime.now(UTC) - timedelta(minutes=10)
    spans = [
        span(trace_id=new_trace_id(), start=start, model=model, trace={"environment": env})
        for model, env in (("no-such-a", "prod"), ("no-such-b", "staging"))
    ]
    response = await client.post(
        "/v1/traces", json={"spans": spans}, headers=bearer(workspace.api_key)
    )
    assert response.status_code == 200 and response.json()["rejected"] == [], response.text

    async def models(**params: str) -> list[str]:
        response = await workspace.owner.get(
            f"/api/v1/projects/{workspace.project_id}/unpriced-models", params=params
        )
        assert response.status_code == 200, response.text
        return sorted(row["model"] for row in response.json())

    assert await models() == ["no-such-a", "no-such-b"]
    assert await models(environment="staging") == ["no-such-b"]
    assert await models(environment="dev") == []

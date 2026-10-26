"""Completing a review item: where the trace first went wrong.

Failure mode: a reviewer of a trace-level item names a span of some other trace, and the label
is stored against a span the trace never had, so the failing-span rollup counts it under the
wrong name.
"""

from typing import Any

import httpx

from tests.helpers import bearer, create_workspace, new_span_id, new_trace_id, span


async def test_a_completion_names_only_a_span_of_the_items_trace(
    browser_factory: Any, client: httpx.AsyncClient
) -> None:
    workspace = await create_workspace(await browser_factory("owner@example.com"))
    owner, project = workspace.owner, f"/api/v1/projects/{workspace.project_id}"
    reviewed, other = new_trace_id(), new_trace_id()
    own_span, foreign_span = new_span_id(), new_span_id()
    ingested = await client.post(
        "/v1/traces",
        json={
            "spans": [
                span(trace_id=reviewed, span_id=own_span),
                span(trace_id=other, span_id=foreign_span),
            ]
        },
        headers=bearer(workspace.api_key),
    )
    assert ingested.status_code == 200, ingested.text
    config = await owner.post(
        f"{project}/score-configs/failure-category", json={"categories": ["none", "format"]}
    )
    assert config.status_code == 201, config.text
    queue = await owner.post(
        f"{project}/annotation-queues",
        json={"name": "triage", "score_config_ids": [config.json()["id"]]},
    )
    assert queue.status_code == 201, queue.text
    queue_path = f"{project}/annotation-queues/{queue.json()['id']}"
    added = await owner.post(f"{queue_path}/items", json={"trace_ids": [reviewed]})
    assert added.status_code == 200, added.text
    item = await owner.post(f"{queue_path}/claim")
    assert item.status_code == 200, item.text
    complete = f"{queue_path}/items/{item.json()['id']}/complete"

    def body(span_id: str) -> dict[str, Any]:
        return {
            "scores": [{"name": "failure_category", "string_value": "format", "span_id": span_id}]
        }

    refused = await owner.post(complete, json=body(foreign_span))
    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "SPAN_NOT_IN_TRACE"

    done = await owner.post(complete, json=body(own_span))
    assert done.status_code == 200, done.text
    scores = await owner.get(f"{project}/traces/{reviewed}/scores")
    assert [score["span_id"] for score in scores.json()] == [own_span]

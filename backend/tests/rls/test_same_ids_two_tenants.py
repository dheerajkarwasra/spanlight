"""Two tenants that use the same ids and names stay independent.

A trace id, a score id (clients choose them) and a prompt or dataset name (unique per project,
not globally) can legitimately be the same in two projects. Every API must then answer from the
caller's own project, and a change in one project must not reach the other. Each pair below is
created through the real API by the owners of two different organizations, with different
content under the shared id or name, so a lookup that forgot the project would return the wrong
row (or a unique violation) rather than pass by luck.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.alerts.jobs import evaluate_all
from app.budgets.guard import StateBudgetGuard
from app.budgets.queries import trace_dimensions
from app.config import Settings
from app.db.rls import bind_project
from tests.helpers import (
    Browser,
    Workspace,
    bearer,
    create_api_key,
    create_workspace,
    new_trace_id,
    span,
)

TRACE_ID = new_trace_id()
SCORE_ID = str(uuid.uuid4())
PROMPT = "greeting"
DATASET = "golden"
METADATA = {"customer": "shared"}
SIDES = ("alpha", "beta")


class Tenant:
    """One organization with a project, and what it created under the shared ids."""

    def __init__(self, label: str, workspace: Workspace) -> None:
        self.label = label
        self.owner = workspace.owner
        self.workspace = workspace
        self.project = f"/api/v1/projects/{workspace.project_id}"
        self.score_value = 1 if label == SIDES[0] else 3  # different, so a mix-up shows
        self.input_tokens = 1000 if label == SIDES[0] else 3000


@pytest.fixture
async def tenants(browser_factory: Any, client: httpx.AsyncClient) -> tuple[Tenant, Tenant]:
    built = []
    for label in SIDES:
        owner: Browser = await browser_factory(f"{label}@example.com")
        workspace = await create_workspace(owner, org_name=f"Org {label}")
        tenant = Tenant(label, workspace)
        await create_shared(tenant, client)
        built.append(tenant)
    return built[0], built[1]


async def create_shared(tenant: Tenant, client: httpx.AsyncClient) -> None:
    """The same trace id, score id, prompt name and dataset name, with this tenant's content."""
    label, owner = tenant.label, tenant.owner
    trace = span(
        trace_id=TRACE_ID,
        input={"messages": [{"role": "user", "content": f"question from {label}"}]},
        usage={"input_tokens": tenant.input_tokens, "output_tokens": 500, "cached_tokens": 0},
        trace={"name": f"chat of {label}", "environment": "prod", "metadata": METADATA},
    )
    ingested = await client.post(
        "/v1/traces", json={"spans": [trace]}, headers=bearer(tenant.workspace.api_key)
    )
    assert ingested.status_code == 200, ingested.text
    score = {"id": SCORE_ID, "name": "quality", "value": tenant.score_value, "comment": label}
    scored = await owner.post(f"{tenant.project}/traces/{TRACE_ID}/scores", json=score)
    assert scored.status_code == 201, scored.text
    template = {"kind": "text", "text": f"Hello from {label}"}
    prompt = await owner.post(
        f"{tenant.project}/prompts", json={"name": PROMPT, "kind": "text", "template": template}
    )
    assert prompt.status_code == 201, prompt.text
    await put_label(tenant, "production", 1)
    dataset = await owner.post(
        f"{tenant.project}/datasets", json={"name": DATASET, "description": label}
    )
    assert dataset.status_code == 201, dataset.text
    items = {"items": [{"input": f"input of {label}", "source_trace_id": TRACE_ID}]}
    added = await owner.post(f"{tenant.project}/datasets/{dataset.json()['id']}/items", json=items)
    assert added.status_code == 201, added.text


async def put_label(tenant: Tenant, label: str, version: int) -> None:
    http = tenant.owner.http
    token = http.cookies.get("spl_csrf")
    response = await http.put(
        f"{tenant.project}/prompts/{PROMPT}/labels/{label}",
        json={"version": version},
        headers={"X-CSRF-Token": token} if token else {},
    )
    assert response.status_code == 200, response.text


async def get_json(tenant: Tenant, path: str) -> Any:
    response = await tenant.owner.get(f"{tenant.project}/{path}")
    assert response.status_code == 200, (path, response.text)
    return response.json()


async def dataset_of(tenant: Tenant) -> dict[str, Any]:
    datasets = (await get_json(tenant, "datasets"))["items"]
    assert [dataset["name"] for dataset in datasets] == [DATASET]
    found: dict[str, Any] = datasets[0]
    return found


async def test_the_same_ids_and_names_read_back_each_tenants_own_rows(
    tenants: tuple[Tenant, Tenant],
) -> None:
    for tenant in tenants:
        label = tenant.label
        trace = await get_json(tenant, f"traces/{TRACE_ID}")
        assert trace["name"] == f"chat of {label}"
        assert f"question from {label}" in str(trace["spans"][0]["input"])

        scores = await get_json(tenant, f"traces/{TRACE_ID}/scores")
        assert [(score["id"], score["comment"]) for score in scores] == [(SCORE_ID, label)]
        assert float(scores[0]["value"]) == tenant.score_value

        prompt = await get_json(tenant, f"prompts/{PROMPT}")
        assert prompt["latest"]["template"]["text"] == f"Hello from {label}"
        assert prompt["labels"] == [{"label": "production", "version": 1}]

        dataset = await dataset_of(tenant)
        assert (dataset["description"], dataset["item_count"]) == (label, 1)
        items = await get_json(tenant, f"datasets/{dataset['id']}/items")
        assert [item["input"] for item in items["items"]] == [f"input of {label}"]


async def test_the_two_trace_lists_each_hold_one_trace(tenants: tuple[Tenant, Tenant]) -> None:
    for tenant in tenants:
        listed = await get_json(tenant, "traces")
        assert [trace["trace_id"] for trace in listed["items"]] == [TRACE_ID]
        assert [trace["name"] for trace in listed["items"]] == [f"chat of {tenant.label}"]


async def test_changing_the_shared_names_in_one_project_leaves_the_other(
    tenants: tuple[Tenant, Tenant],
) -> None:
    first, second = tenants
    new_version = {"template": {"kind": "text", "text": "Hello again from alpha"}}
    created = await first.owner.post(f"{first.project}/prompts/{PROMPT}/versions", json=new_version)
    assert created.status_code == 201, created.text
    await put_label(first, "production", 2)

    moved = await get_json(first, f"prompts/{PROMPT}")
    assert moved["latest"]["version"] == 2
    assert moved["labels"] == [{"label": "production", "version": 2}]
    untouched = await get_json(second, f"prompts/{PROMPT}")
    assert untouched["latest"]["version"] == 1
    assert untouched["latest"]["template"]["text"] == "Hello from beta"
    assert untouched["labels"] == [{"label": "production", "version": 1}]


async def test_deleting_in_one_project_leaves_the_other_untouched(
    tenants: tuple[Tenant, Tenant],
) -> None:
    first, second = tenants
    dataset = await dataset_of(first)

    assert (await first.owner.delete(f"{first.project}/scores/{SCORE_ID}")).status_code == 204
    assert (await first.owner.delete(f"{first.project}/prompts/{PROMPT}")).status_code == 204
    assert (
        await first.owner.delete(f"{first.project}/datasets/{dataset['id']}")
    ).status_code == 204

    assert await get_json(first, f"traces/{TRACE_ID}/scores") == []
    assert (await first.owner.get(f"{first.project}/prompts/{PROMPT}")).status_code == 404
    assert (await get_json(first, "datasets"))["items"] == []
    scores = await get_json(second, f"traces/{TRACE_ID}/scores")
    assert [(score["id"], score["comment"]) for score in scores] == [(SCORE_ID, "beta")]
    assert (await get_json(second, f"prompts/{PROMPT}"))["latest"]["version"] == 1
    kept = await dataset_of(second)
    assert (kept["description"], kept["item_count"]) == ("beta", 1)


async def test_a_retried_score_id_is_idempotent_only_inside_its_own_project(
    tenants: tuple[Tenant, Tenant], client: httpx.AsyncClient
) -> None:
    """The same score id sent by key to both projects: each stores its own, none overwrites."""
    first, second = tenants
    fresh = str(uuid.uuid4())
    for tenant in tenants:
        key = await create_api_key(
            tenant.owner, tenant.workspace.project_id, scopes=["scores:write"]
        )
        score = {"id": fresh, "trace_id": TRACE_ID, "name": "tone", "value": tenant.score_value}
        for _ in range(2):  # the retry changes nothing
            sent = await client.post(
                "/v1/scores",
                json={"scores": [score]},
                headers=bearer(key["secret"]),
            )
            assert sent.status_code == 200, sent.text
            assert sent.json()["rejected"] == []

    for tenant in (first, second):
        scores = await get_json(tenant, f"traces/{TRACE_ID}/scores")
        tone = [score for score in scores if score["name"] == "tone"]
        assert [(score["id"], float(score["value"])) for score in tone] == [
            (fresh, float(tenant.score_value))
        ]


async def test_attribution_of_each_tenant_shows_only_its_own_metadata(
    tenants: tuple[Tenant, Tenant],
) -> None:
    """The same metadata key and value in two projects: each project's attribution adds up its
    own trace only (the tenants differ in token counts, so a mix-up would show)."""
    for tenant in tenants:
        body = await get_json(tenant, "attribution?group_by=metadata&key=customer")
        assert body["source"] == "raw"
        assert [
            (item["value"], item["traces"], item["input_tokens"]) for item in body["items"]
        ] == [("shared", 1, tenant.input_tokens)]
        assert body["totals"]["input_tokens"] == tenant.input_tokens
        assert body["none"] is None


async def test_a_metadata_budget_in_one_project_never_blocks_the_other_with_the_same_pair(
    tenants: tuple[Tenant, Tenant],
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """A firing `metadata customer=shared` budget in the first project: the guard blocks a call
    bound to that project and allows the same pair bound to the second, whether the pair comes
    from the header or from the second project's own stored trace."""
    first, _ = tenants
    body = {
        "name": "Shared cap",
        "scope": "metadata",
        "scope_id": "customer=shared",
        "period": "monthly",
        "amount_usd": "0.0001",
        "action": "block",
        "channel_ids": [],
    }
    created = await first.owner.post(f"{first.project}/budgets", json=body)
    assert created.status_code == 201, created.text
    run = await evaluate_all(session_factory, settings, now=datetime.now(UTC))
    assert (run.errors, run.projects_failed) == (0, 0)

    decisions = {}
    for tenant in tenants:
        project_id = uuid.UUID(tenant.workspace.project_id)
        async with session_factory() as db, db.begin():
            await bind_project(db, project_id)
            stored = await trace_dimensions(db, project_id, TRACE_ID)
            assert stored == (f"chat of {tenant.label}", METADATA)
            decision = await StateBudgetGuard().check(
                db, project_id, None, "gpt-4o-mini", None, datetime.now(UTC), metadata=stored[1]
            )
        decisions[tenant.label] = decision.allowed
    assert decisions == {"alpha": False, "beta": True}

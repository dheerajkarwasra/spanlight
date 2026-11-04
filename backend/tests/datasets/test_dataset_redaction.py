"""Dataset items are redacted like ingested spans: every write path (JSON add, JSONL and CSV
upload, item edit) stores `input`, `expected_output` and `metadata` with secrets replaced, and
with emails replaced too when the project opts in (`redact_contact_info`)."""

import json
from typing import Any

import pytest

from app.core.redact import REDACTED
from tests.helpers import Browser, Workspace, create_workspace

SECRET = "sk-" + "proj-" + "A1b2C3d4E5f6G7h8I9j0KlMn"
EMAIL = "jane@example.com"
TEXT = f"key {SECRET} mail {EMAIL}"


@pytest.fixture
async def workspace(browser_factory: Any) -> Workspace:
    owner: Browser = await browser_factory("owner@example.com")
    return await create_workspace(owner)


async def _dataset(workspace: Workspace, *, contacts: bool) -> str:
    project = f"/api/v1/projects/{workspace.project_id}"
    if contacts:
        patched = await workspace.owner.patch(project, json={"redact_contact_info": True})
        assert patched.status_code == 200, patched.text
    created = await workspace.owner.post(f"{project}/datasets", json={"name": "golden"})
    assert created.status_code == 201, created.text
    return f"{project}/datasets/{created.json()['id']}"


async def _stored(workspace: Workspace, url: str) -> dict[str, Any]:
    listed = await workspace.owner.get(f"{url}/items")
    assert listed.status_code == 200, listed.text
    (item,) = listed.json()["items"]
    return dict(item)


def _expected(contacts: bool) -> str:
    return f"key {REDACTED} mail {REDACTED if contacts else EMAIL}"


def _assert_redacted(item: dict[str, Any], contacts: bool, *, fields: tuple[str, ...]) -> None:
    for field in fields:
        stored = json.dumps(item[field])
        assert SECRET not in stored, field
        assert _expected(contacts) in stored, (field, stored)


@pytest.mark.parametrize("contacts", [False, True])
async def test_items_added_as_json_are_redacted(workspace: Workspace, contacts: bool) -> None:
    url = await _dataset(workspace, contacts=contacts)
    item = {"input": {"q": TEXT}, "expected_output": TEXT, "metadata": {"note": TEXT}}

    added = await workspace.owner.post(f"{url}/items", json={"items": [item]})

    assert added.status_code == 201, added.text
    assert added.json() == {"accepted": 1}
    stored = await _stored(workspace, url)
    _assert_redacted(stored, contacts, fields=("input", "expected_output", "metadata"))


@pytest.mark.parametrize("contacts", [False, True])
async def test_items_uploaded_as_jsonl_are_redacted(workspace: Workspace, contacts: bool) -> None:
    url = await _dataset(workspace, contacts=contacts)
    line = json.dumps({"input": TEXT, "expected_output": TEXT, "metadata": {"note": TEXT}})

    uploaded = await workspace.owner.post(
        f"{url}/items/upload",
        files={"file": ("items.jsonl", line.encode(), "application/x-ndjson")},
    )

    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json() == {"accepted": 1, "rejected": []}
    stored = await _stored(workspace, url)
    _assert_redacted(stored, contacts, fields=("input", "expected_output", "metadata"))


@pytest.mark.parametrize("contacts", [False, True])
async def test_items_uploaded_as_csv_are_redacted(workspace: Workspace, contacts: bool) -> None:
    url = await _dataset(workspace, contacts=contacts)
    csv = f"input,expected_output,note\n{TEXT},{TEXT},{TEXT}\n"

    uploaded = await workspace.owner.post(
        f"{url}/items/upload", files={"file": ("items.csv", csv.encode(), "text/csv")}
    )

    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json() == {"accepted": 1, "rejected": []}
    stored = await _stored(workspace, url)
    _assert_redacted(stored, contacts, fields=("input", "expected_output", "metadata"))


@pytest.mark.parametrize("contacts", [False, True])
async def test_an_item_edit_is_redacted(workspace: Workspace, contacts: bool) -> None:
    url = await _dataset(workspace, contacts=contacts)
    added = await workspace.owner.post(f"{url}/items", json={"items": [{"input": "plain"}]})
    assert added.status_code == 201, added.text
    item_id = (await _stored(workspace, url))["id"]

    edited = await workspace.owner.patch(
        f"{url}/items/{item_id}", json={"expected_output": TEXT, "metadata": {TEXT: TEXT}}
    )

    assert edited.status_code == 200, edited.text
    _assert_redacted(edited.json(), contacts, fields=("expected_output", "metadata"))
    stored = await _stored(workspace, url)
    _assert_redacted(stored, contacts, fields=("expected_output", "metadata"))
    assert stored["input"] == "plain"

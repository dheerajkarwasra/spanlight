"""`backend/openapi.json` is the committed HTTP contract: a change to it is always deliberate."""

import difflib

import pytest

from scripts.update_openapi import SNAPSHOT_PATH, UPDATE_COMMAND, build_app, render_openapi

DIFF_LINES_SHOWN = 40


def test_openapi_matches_snapshot() -> None:
    current = render_openapi(build_app())
    committed = SNAPSHOT_PATH.read_text(encoding="utf-8") if SNAPSHOT_PATH.exists() else ""
    if current == committed:
        return

    diff = difflib.unified_diff(
        committed.splitlines(),
        current.splitlines(),
        fromfile="openapi.json (committed)",
        tofile="openapi.json (current app)",
        lineterm="",
        n=2,
    )
    shown = list(diff)[:DIFF_LINES_SHOWN]
    problem = (
        "backend/openapi.json does not exist."
        if not SNAPSHOT_PATH.exists()
        else "backend/openapi.json is out of date: the API contract changed."
    )
    pytest.fail(
        f"{problem}\n"
        f"If the change is intended, run this from backend/ and commit the result:\n"
        f"    {UPDATE_COMMAND}\n\n" + "\n".join(shown),
        pytrace=False,
    )


def test_openapi_names_no_internal_spec_section() -> None:
    """Schema docstrings become the public API reference: a pointer into an internal design
    document (`spec §8.2`) means nothing to its readers."""
    current = render_openapi(build_app())

    assert "§" not in current

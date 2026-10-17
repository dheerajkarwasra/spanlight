"""Offline tests of the Doctor accuracy harness: no network, no Anthropic key, no stack.

Every test runs with `DOCTOR_HARNESS_STATE` pointed at a temporary directory and without
`ANTHROPIC_API_KEY`, so nothing here can read the owner's harness state or spend money.
"""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "pilot" / "state.json"
    monkeypatch.setenv("DOCTOR_HARNESS_STATE", str(state))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return state

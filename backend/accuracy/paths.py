"""Where the harness keeps its state, its spend ledger and its results.

Nothing the harness writes lives in tracked files. The pilot's state file (account, org,
credential id, personal access token, generated password) is named by `DOCTOR_HARNESS_STATE`,
which defaults to the git-ignored `DEFAULT_STATE` under the repository root. The Doctor
harness's own state and the shared spend ledger sit in the same directory, so one variable
moves all three. A relative path resolves against the repository root, so the commands work
from `backend/` and from the root alike.

The Anthropic key is read from `ANTHROPIC_API_KEY` at call time, never from a file.
"""

import os
from dataclasses import dataclass
from pathlib import Path

STATE_ENV = "DOCTOR_HARNESS_STATE"
KEY_ENV = "ANTHROPIC_API_KEY"
DEFAULT_STATE = ".local/pilot/state.json"

REPO = Path(__file__).resolve().parents[2]
RESULTS_DIR = Path(__file__).resolve().parent / "results"
"""`backend/accuracy/results/`, git-ignored: collected rows, one JSON Lines file per run."""


@dataclass(frozen=True, slots=True)
class HarnessPaths:
    state: Path
    """The pilot's state: written by `accuracy.setup`, read by the Doctor harness."""
    doctor_state: Path
    """The Doctor harness's projects, keys, models and run records."""
    ledger: Path
    """The spend ledger every paid call reserves against (shared with other pilot harnesses)."""


def harness_paths() -> HarnessPaths:
    """The paths for this process, read from the environment at call time."""
    raw = os.environ.get(STATE_ENV, "").strip() or DEFAULT_STATE
    state = Path(raw).expanduser()
    if not state.is_absolute():
        state = REPO / state
    return HarnessPaths(
        state=state,
        doctor_state=state.parent / "doctor" / "state.json",
        ledger=state.parent / "ledger.json",
    )


class MissingKeyError(RuntimeError):
    """`ANTHROPIC_API_KEY` is not set; no paid call can be made."""


def anthropic_api_key() -> str:
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        raise MissingKeyError(f"{KEY_ENV} is not set in the environment")
    return key

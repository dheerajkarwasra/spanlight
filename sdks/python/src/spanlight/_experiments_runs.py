"""Starting a run and following it to its end, and how a run ends."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ._experiments_api import Api, ExperimentError
from ._experiments_report import check_thresholds

# Consecutive failed polls before the runner gives up: a deploy of the server must not fail a gate.
_POLL_RETRIES = 3


@dataclass(frozen=True)
class ExperimentOutcome:
    """How one run ended.

    ``status`` is ``done``, ``failed`` or ``timeout``; ``totals`` is the run's totals from the
    API (``None`` when it never produced any); ``failures`` lists the thresholds that were missed.
    """

    run_id: str
    totals: dict[str, Any] | None
    failures: list[str]
    url: str
    status: str = "done"
    error: str | None = None
    title: str = field(default="", compare=False)

    @property
    def passed(self) -> bool:
        """Whether the run finished and every threshold held."""
        return self.status == "done" and not self.failures


def outcome_of(
    final: dict[str, Any] | None,
    run_id: str,
    url: str,
    title: str,
    timeout_s: int,
    fail_below: dict[str, Decimal],
) -> ExperimentOutcome:
    """Turn the last state of a run (``None``: still going at the deadline) into an outcome."""
    if final is None:
        return ExperimentOutcome(
            run_id, None, [], url, "timeout", f"no result after {timeout_s} s", title
        )
    totals = final.get("totals")
    if final["status"] == "failed":
        return ExperimentOutcome(run_id, totals, [], url, "failed", final.get("error"), title)
    failures = check_thresholds(totals or {}, fail_below)
    return ExperimentOutcome(run_id, totals, failures, url, "done", None, title)


def org_of(api: Api) -> str:
    return str(api.request("GET", api.project())["org_id"])


def start_run(
    api: Api, experiment_id: str, version_id: str, env: Mapping[str, str]
) -> dict[str, Any]:
    """Queue a run. Inside GitHub Actions a retried request of one attempt starts only one run."""
    headers: dict[str, str] = {}
    run_id, attempt = env.get("GITHUB_RUN_ID"), env.get("GITHUB_RUN_ATTEMPT")
    if run_id:
        # The whole experiment id keeps two gate steps of one workflow run from sharing a key.
        headers["Idempotency-Key"] = f"{run_id}-{attempt or '1'}-{experiment_id}"
    path = api.project(f"/experiments/{experiment_id}/runs")
    # The run names its prompt version, so a moved label runs the new version in this experiment.
    body = {"target_override": {"prompt_version_id": version_id}}
    started: dict[str, Any] = api.request("POST", path, headers=headers, body=body)
    return started


def wait_idle(
    api: Api,
    experiment_id: str,
    deadline: float,
    poll_s: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> None:
    """Wait until the experiment has no queued or running run: it allows only one at a time."""
    path = api.project(f"/experiments/{experiment_id}/runs")
    while True:
        latest = api.request("GET", path, params={"limit": 1})["items"]
        if not latest or latest[0]["status"] not in {"queued", "running"}:
            return
        if clock() >= deadline:
            raise ExperimentError("another run of this experiment did not finish in time")
        sleep(poll_s)


def wait(
    api: Api,
    experiment_id: str,
    run_id: str,
    deadline: float,
    poll_s: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> dict[str, Any] | None:
    """Poll until the run is ``done`` or ``failed``; ``None`` when the deadline passes first."""
    path = api.project(f"/experiments/{experiment_id}/runs/{run_id}")
    errors = 0
    while True:
        try:
            run: dict[str, Any] = api.request("GET", path)
            errors = 0
            if run["status"] in {"done", "failed"}:
                return run
        except ExperimentError:
            errors += 1
            if errors >= _POLL_RETRIES:
                raise
        if clock() >= deadline:
            return None
        sleep(poll_s)

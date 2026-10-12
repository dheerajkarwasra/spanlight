"""Run a prompt over a dataset on the server and gate on the scores.

Use it as ``python -m spanlight._experiments``.

The runner talks to the dashboard API (``/api/v1``) with a personal access token. It finds the
dataset, prompt version and evaluators by name, reuses or creates the experiment for that
combination, starts a run and polls it until it ends. Exit codes: 0 every threshold held, 1 a
score fell below its ``--fail-below`` threshold, 2 the run could not be started, failed or timed
out. The ``eval-gate`` GitHub Action wraps this module.

The token is read from ``SPANLIGHT_TOKEN`` and never printed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from ._experiments_api import (
    Api,
    ExperimentError,
    ResolvedPrompt,
    resolve_dataset,
    resolve_evaluators,
    resolve_prompt,
)
from ._experiments_parse import PromptRef, parse_fail_below, parse_prompt_ref
from ._experiments_report import render_markdown
from ._experiments_runs import (
    ExperimentOutcome,
    org_of,
    outcome_of,
    start_run,
    wait,
    wait_idle,
)
from ._experiments_setup import experiment_for, normalize_credential_id

__all__ = [
    "ExperimentError",
    "ExperimentOutcome",
    "PromptRef",
    "main",
    "parse_fail_below",
    "parse_prompt_ref",
    "run_experiment",
]

EXIT_PASSED = 0
EXIT_THRESHOLD = 1
EXIT_FAILED = 2
_MAX_COST_CEILING = Decimal(1000)


def _prepare(
    api: Api,
    dataset: str,
    prompt: str,
    evaluators: list[str],
    credential_id: str,
    model: str,
    max_cost_usd: Decimal | None,
) -> tuple[dict[str, Any], ResolvedPrompt]:
    """Resolve the names to ids and find or create the experiment."""
    dataset_row = resolve_dataset(api, dataset)
    version = resolve_prompt(api, parse_prompt_ref(prompt))
    experiment = experiment_for(
        api,
        dataset_row,
        version,
        evaluators,
        resolve_evaluators(api, evaluators),
        credential_id=credential_id,
        model=model,
        max_cost_usd=max_cost_usd,
    )
    return experiment, version


def run_experiment(
    *,
    host: str,
    token: str,
    project_id: str,
    dataset: str,
    prompt: str,
    evaluators: list[str],
    fail_below: dict[str, Decimal],
    credential_id: str,
    model: str,
    max_cost_usd: Decimal | None = None,
    timeout_s: int = 1800,
    poll_s: float = 5,
    transport: httpx.BaseTransport | None = None,
    environ: Mapping[str, str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> ExperimentOutcome:
    """Run ``prompt`` (``name``, ``name@label`` or ``name:version``) over ``dataset`` and score it.

    ``credential_id`` and ``model`` choose the provider the prompt runs on and, with the
    evaluators, which experiment is used (a different combination is a different experiment).
    ``max_cost_usd`` caps a run and applies only when the experiment is created. The whole call,
    including waiting for an earlier run of the same experiment, is bounded by ``timeout_s``.
    Raises :class:`ExperimentError` when the run cannot be set up; a run that fails or times out
    is returned with that ``status``.
    """
    env = os.environ if environ is None else environ
    credential_id = normalize_credential_id(credential_id)
    api = Api(host=host, token=token, project_id=project_id, transport=transport)
    try:
        deadline = clock() + timeout_s
        experiment, version = _prepare(
            api, dataset, prompt, evaluators, credential_id, model, max_cost_usd
        )
        url = f"{host.rstrip('/')}/{org_of(api)}/{project_id}/experiments/{experiment['id']}"
        wait_idle(api, experiment["id"], deadline, poll_s, sleep, clock)
        run = start_run(api, experiment["id"], version.version_id, env)
        try:
            final = wait(api, experiment["id"], run["id"], deadline, poll_s, sleep, clock)
        except ExperimentError as error:
            # The run exists: report it with its id rather than lose it.
            final = {"status": "failed", "error": str(error), "totals": None}
    finally:
        api.close()
    title = f"{dataset} / {version.name} v{version.version}"
    return outcome_of(final, run["id"], url, title, timeout_s, fail_below)


def _poll_seconds(raw: str) -> float:
    """``--poll``: at least one second, so the gate cannot hammer the server."""
    value = float(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1 second")
    return value


def _max_cost(raw: str) -> Decimal:
    """``--max-cost-usd``: a positive amount of at most the server's ceiling of 1000."""
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise argparse.ArgumentTypeError("must be a number") from None
    if not value.is_finite() or not 0 < value <= _MAX_COST_CEILING:
        raise argparse.ArgumentTypeError("must be above 0 and at most 1000")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m spanlight._experiments",
        description="Run a prompt over a dataset and fail when a score is below its threshold. "
        "The access token is read from SPANLIGHT_TOKEN.",
    )
    env = os.environ.get
    parser.add_argument("--host", default=env("SPANLIGHT_HOST"), help="Spanlight URL (https)")
    parser.add_argument("--project-id", default=env("SPANLIGHT_PROJECT_ID"))
    parser.add_argument("--dataset", required=True, help="dataset name")
    parser.add_argument("--prompt", required=True, help="name, name@label or name:version")
    parser.add_argument("--evaluators", default="", help="comma-separated evaluator names")
    parser.add_argument("--fail-below", default="", help="e.g. relevance=0.8,format=1")
    parser.add_argument("--credential-id", default=None, help="provider credential to run on")
    parser.add_argument("--model", default=None, help="model to run the prompt on")
    parser.add_argument(
        "--max-cost-usd", type=_max_cost, default=None, help="run cost cap (new experiments)"
    )
    parser.add_argument("--timeout", type=int, default=1800, help="seconds to wait in total")
    parser.add_argument("--poll", type=_poll_seconds, default=5.0, help="seconds between polls")
    return parser


def _publish(outcome: ExperimentOutcome, markdown: str) -> None:
    """Print the report; inside GitHub Actions also fill the step summary and step outputs."""
    print(markdown)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")
    outputs = os.environ.get("GITHUB_OUTPUT")
    if outputs:
        with open(outputs, "a", encoding="utf-8") as handle:
            handle.write(f"run-id={outcome.run_id}\nrun-url={outcome.url}\n")


def _missing_settings(args: argparse.Namespace, token: str | None) -> list[str]:
    """What the caller left out, as messages."""
    checks = (
        ("--host or SPANLIGHT_HOST is required", args.host),
        ("--project-id or SPANLIGHT_PROJECT_ID is required", args.project_id),
        ("--credential-id is required", args.credential_id),
        ("--model is required", args.model),
        ("SPANLIGHT_TOKEN is required", token),
    )
    return [message for message, value in checks if not value]


def _gate(args: argparse.Namespace, token: str) -> int:
    """Run the experiment, publish the report and return the exit code."""
    fail_below = parse_fail_below(args.fail_below)
    outcome = run_experiment(
        host=args.host,
        token=token,
        project_id=args.project_id,
        dataset=args.dataset,
        prompt=args.prompt,
        evaluators=[name.strip() for name in args.evaluators.split(",") if name.strip()],
        fail_below=fail_below,
        credential_id=args.credential_id,
        model=args.model,
        max_cost_usd=args.max_cost_usd,
        timeout_s=args.timeout,
        poll_s=args.poll,
    )
    report = render_markdown(
        title=outcome.title,
        status=outcome.status,
        error=outcome.error,
        url=outcome.url,
        totals=outcome.totals,
        fail_below=fail_below,
        failures=outcome.failures,
    )
    _publish(outcome, report)
    if outcome.status != "done":
        return EXIT_FAILED
    return EXIT_PASSED if outcome.passed else EXIT_THRESHOLD


def main(argv: Sequence[str] | None = None) -> int:
    """Run the gate. Returns the exit code: 0 passed, 1 a threshold missed, 2 anything else."""
    args = _parser().parse_args(argv)
    token = os.environ.get("SPANLIGHT_TOKEN")
    problems = _missing_settings(args, token)
    if problems:
        print("error: " + "; ".join(problems), file=sys.stderr)
        return EXIT_FAILED
    try:
        return _gate(args, token or "")
    except (ExperimentError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
    except Exception as error:
        # Exit 1 means "a score fell below its threshold", so a bug or an answer of an
        # unexpected shape must not look like one. The type only: a repr could carry a header.
        print(f"error: unexpected {type(error).__name__}", file=sys.stderr)
    return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())

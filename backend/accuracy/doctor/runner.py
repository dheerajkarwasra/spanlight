"""Drive the Doctor accuracy harness: plan, set up, baseline, run, collect.

From `backend/`, with the local SDK added for the live steps:

    RUN="uv run --with ../sdks/python python -m"
    $RUN accuracy.doctor.runner --plan              # every scenario, mode, project, est. cost
    $RUN accuracy.doctor.runner --setup             # projects, keys, routes, fault profiles
    $RUN accuracy.doctor.runner --baseline          # model probe + backdated synthetic baselines
    $RUN accuracy.doctor.trigger --rollup-only      # then wait for it to report done
    $RUN accuracy.doctor.runner --run [--only id,id]   # live traffic, trace ids kept in state
    $RUN accuracy.doctor.trigger                    # rollup, detectors, poll detector-runs
    $RUN accuracy.doctor.runner --collect           # findings per project -> results/*.jsonl
    $RUN accuracy.doctor.score                      # precision / recall per detector

State: the pilot's state file (`DOCTOR_HARNESS_STATE`, written by `python -m accuracy.setup`) is
read, never written; the harness keeps its own state beside it. Every paid call goes through the
spend ledger; a call that could pass its soft cap is refused before it is made.
"""

import argparse
import sys
import time
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any

from accuracy.dashboard import SetupError
from accuracy.doctor.clients import direct_client, guarded_create
from accuracy.doctor.collect import collect
from accuracy.doctor.common import HAIKU, HAIKU45, doctor_state, now_utc, spend_ledger
from accuracy.doctor.context import RunContext, RunResult
from accuracy.doctor.runner_setup import scoped, setup
from accuracy.doctor.scenario_types import Scenario
from accuracy.doctor.scenarios import CONTROL, DETECTORS, SCENARIOS, total_est_cost
from accuracy.paths import MissingKeyError
from accuracy.setup import HOST, State

PROBE_MODELS = (HAIKU, HAIKU45)
SHORT_WINDOW = timedelta(minutes=15)


# --------------------------------------------------------------------------- plan


def _plan_row(s: Scenario) -> tuple[str, ...]:
    return (
        s.id,
        s.label,
        s.mode,
        "yes" if s.needs_baseline else "",
        f"{int(s.window.total_seconds() // 60)} min",
        s.project + (f" (+{len(s.extra_projects)})" if s.extra_projects else ""),
        f"{s.est_cost_usd:.3f}",
        s.variant,
    )


def plan(scenarios: list[Scenario]) -> None:
    rows = [_plan_row(s) for s in scenarios]
    headers = ("scenario", "label", "mode", "baseline", "window", "project", "est $", "variant")
    widths = [max(len(str(r[i])) for r in (headers, *rows)) for i in range(len(headers))]
    print("| " + " | ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)) + " |")
    print("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    for row in rows:
        print("| " + " | ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)) + " |")
    print()
    print(f"{len(scenarios)} scenarios")
    for detector in (*DETECTORS, CONTROL):
        group = [s for s in scenarios if s.detector == detector]
        pos = sum(1 for s in group if s.label == "pos")
        neg = sum(1 for s in group if s.label == "neg")
        print(f"  {detector:32s} pos={pos} neg={neg}")
    print(f"estimated total cost: ${total_est_cost(scenarios):.3f} (rough, at published rates)")
    print(f"ledger now: {spend_ledger().totals()}")


# --------------------------------------------------------------------------- contexts


def _context(state: State, scenario: Scenario) -> RunContext:
    projects = scoped(state, scenario.org).get("projects", {})
    missing = [n for n in scenario.project_names if n not in projects]
    if missing:
        raise SetupError(f"{scenario.id}: projects not set up: {missing} (run --setup)")
    return RunContext(
        scenario_id=scenario.id,
        environment=scenario.environment,
        projects={n: projects[n] for n in scenario.project_names},
        models=state.get("models", {}),
        result=RunResult(),
        host=HOST,
    )


def _store_result(
    state: State, bucket: str, scenario: Scenario, ctx: RunContext, error: str | None
) -> None:
    runs = state.get(bucket, {})
    result = ctx.result
    runs[scenario.id] = {
        "project": scenario.project,
        "projects": list(scenario.project_names),
        "environment": scenario.environment,
        "started_at": result.started_at.isoformat() if result.started_at else None,
        "ended_at": result.ended_at.isoformat() if result.ended_at else None,
        "calls": result.calls,
        "failures": result.failures,
        "trace_ids": result.trace_ids,
        "notes": result.notes,
        "facts": result.facts,
        "error": error,
        "nonce": ctx.nonce,
    }
    state.set(bucket, runs)


def _execute(step: Any, ctx: RunContext) -> str | None:
    """Run one scenario step; a failure is returned for the row, and the next scenario runs."""
    ctx.result.started_at = now_utc()
    error = None
    try:
        step(ctx)
    except Exception as exc:  # noqa: BLE001 - recorded on the scenario's row, never swallowed
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
    ctx.result.ended_at = now_utc()
    return error


# --------------------------------------------------------------------------- baseline


def probe_models(state: State) -> dict[str, str]:
    """One tiny real call per model to learn the name the provider answers with.

    The SDK stores the response's `model`, the gateway the provider's too; a 400 with no
    response keeps the requested name. Requesting the answered name makes every span of a
    scenario, failed or not, share one model and so one baseline slice.
    """
    models = dict(state.get("models", {}))
    client = direct_client()
    for requested in PROBE_MODELS:
        if requested in models:
            continue
        response = guarded_create(
            client,
            scenario="doctor:probe",
            model=requested,
            path="sdk",
            est_prompt_tokens=20,
            max_tokens=5,
            messages=[{"role": "user", "content": "Reply with the single word OK."}],
        )
        models[requested] = response.model
        print(f"probe {requested} -> {response.model}")
    state.set("models", models)
    return models


def baseline(scenarios: list[Scenario]) -> None:
    state = doctor_state()
    probe_models(state)
    for scenario in scenarios:
        if scenario.baseline is None:
            continue
        ctx = _context(state, scenario)
        error = _execute(scenario.baseline, ctx)
        _store_result(state, "baselines", scenario, ctx, error)
        status = "ERR" if error else "ok "
        print(f"{status} {scenario.id:48s} {error or '; '.join(ctx.result.notes)}")
    print("baselines ingested. Now run the trigger with --rollup-only and wait for it to finish.")


# --------------------------------------------------------------------------- run


def _spent() -> Decimal:
    return Decimal(spend_ledger().totals()["spent_usd"])


def run(scenarios: list[Scenario]) -> None:
    state = doctor_state()
    if not state.get("models"):
        raise SetupError("no probed models in state: run --baseline first")
    ordered = sorted(scenarios, key=lambda s: -s.window.total_seconds())
    before = _spent()
    first_short: Any = None
    started_all = time.monotonic()
    for scenario in ordered:
        ctx = _context(state, scenario)
        if scenario.window <= SHORT_WINDOW and first_short is None:
            first_short = now_utc()
        spent_before = _spent()
        tick = time.monotonic()
        error = _execute(scenario.run, ctx)
        _store_result(state, "runs", scenario, ctx, error)
        result = ctx.result
        print(
            f"{'ERR' if error else 'ok '} {scenario.id:48s} {time.monotonic() - tick:6.1f}s "
            f"calls={result.calls:3d} failed={result.failures:3d} "
            f"traces={len(result.trace_ids):3d} spent=${_spent() - spent_before:.4f}"
            + (f"  {error}" if error else "")
        )
    elapsed = time.monotonic() - started_all
    print(f"run done in {elapsed / 60:.1f} min, spent ${_spent() - before:.4f}")
    if first_short is not None:
        deadline = first_short + SHORT_WINDOW
        print(
            f"15-minute-window scenarios started at {first_short.isoformat(timespec='seconds')}: "
            f"trigger the detectors before {deadline.isoformat(timespec='seconds')} "
            "(python -m accuracy.doctor.trigger)"
        )


# --------------------------------------------------------------------------- main


def select(only: str | None) -> list[Scenario]:
    if not only:
        return list(SCENARIOS)
    wanted = [w.strip() for w in only.split(",") if w.strip()]
    chosen = [s for s in SCENARIOS if wanted_by(s, wanted)]
    unknown = [w for w in wanted if not any(wanted_by(s, [w]) for s in SCENARIOS)]
    if unknown:
        raise SystemExit(f"unknown scenario(s): {unknown}")
    return chosen


def wanted_by(scenario: Scenario, wanted: list[str]) -> bool:
    return any(w in (scenario.id, scenario.detector, scenario.project) for w in wanted)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_mutually_exclusive_group(required=True)
    steps: dict[str, Callable[[list[Scenario]], object]] = {
        "plan": plan,
        "setup": setup,
        "baseline": baseline,
        "run": run,
        "collect": collect,
    }
    for name in steps:
        group.add_argument(f"--{name}", action="store_true")
    parser.add_argument("--only", help="comma-separated scenario ids, detectors or project names")
    args = parser.parse_args(argv)
    scenarios = select(args.only)
    step = next(fn for name, fn in steps.items() if getattr(args, name))
    try:
        step(scenarios)
    except (SetupError, MissingKeyError) as error:
        print(f"failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

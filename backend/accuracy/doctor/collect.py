"""`runner.py --collect`: the findings of every scenario project, one JSON row per scenario.

A row says whether the scenario's detector fired, which other kinds appeared, and, for a
scenario with an `Expectation`, what the finding showed and whether it met the expectation.
`expectation_met` is pure, so `tests/accuracy/test_collect.py` checks it on hand-made rows.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from accuracy.doctor.common import PatApi, doctor_state, now_utc
from accuracy.doctor.context import SEEDED_INCIDENT_TITLE
from accuracy.doctor.runner_setup import scoped
from accuracy.doctor.scenario_types import Expectation, Scenario
from accuracy.paths import RESULTS_DIR
from accuracy.setup import HOST

__all__ = ["SEEDED_INCIDENT_TITLE", "collect", "collect_row", "expectation_met"]

MAX_EVIDENCE_TRACES = 5


def expectation_met(expect: Expectation | None, row: dict[str, Any]) -> bool | None:
    """Whether a fired row shows what the scenario expects; None when there is nothing to check
    (no expectation, or the detector did not fire, which the label already scores)."""
    if expect is None or not row["fired"]:
        return None
    if expect.rate_limit_kind is not None:
        kinds = row.get("rate_limit_kinds") or []
        if kinds != [expect.rate_limit_kind]:
            return False
    if expect.incident_cited:
        seeded = (row.get("run_facts") or {}).get(SEEDED_INCIDENT_TITLE)
        cited = row.get("incident_citations") or []
        if seeded is None or not any(c.get("title") == seeded and c.get("id") for c in cited):
            return False
    return True


def _rate_limit_kinds(api: PatApi, project_id: str, trace_ids: list[str]) -> list[str | None]:
    """The `rate_limit_kind` of every failed llm span in the first few evidence traces."""
    kinds: set[str | None] = set()
    for trace_id in trace_ids[:MAX_EVIDENCE_TRACES]:
        for span in api.trace(project_id, trace_id).get("spans", []):
            if span.get("kind") == "llm" and span.get("status") == "error":
                kinds.add(span.get("rate_limit_kind"))
    return sorted(kinds, key=lambda kind: kind or "")


def _citation(finding: dict[str, Any]) -> dict[str, Any]:
    metrics = finding.get("evidence", {}).get("metrics", {})
    return {"id": metrics.get("incident_id"), "title": metrics.get("incident_title")}


def _expectation_fields(
    api: PatApi, scenario: Scenario, matches: list[tuple[str, dict[str, Any]]]
) -> dict[str, Any]:
    expect = scenario.expect
    fields: dict[str, Any] = {}
    if expect is None or not matches:
        return fields
    if expect.rate_limit_kind is not None:
        kinds: set[str | None] = set()
        for project_id, finding in matches:
            kinds.update(_rate_limit_kinds(api, project_id, finding["evidence"]["trace_ids"]))
        fields["rate_limit_kinds"] = sorted(kinds, key=lambda kind: kind or "")
    if expect.incident_cited:
        fields["incident_citations"] = [_citation(finding) for _, finding in matches]
    return fields


@dataclass
class _Scan:
    """What the scenario's projects show: every finding kind, the scenario detector's findings
    as `(project_id, finding)`, the projects they fired in and the newest detector run."""

    kinds: set[str] = field(default_factory=set)
    matches: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    fired_in: list[str] = field(default_factory=list)
    last_run: str | None = None


def _scan(api: PatApi, scenario: Scenario, projects: dict[str, Any]) -> _Scan:
    scan = _Scan()
    for name in scenario.project_names:
        entry = projects.get(name)
        if entry is None:
            continue
        findings = api.insights(entry["project_id"], ("open",))
        scan.kinds.update(f["kind"] for f in findings)
        for finding in findings:
            if finding["kind"] == scenario.detector:
                scan.fired_in.append(name)
                scan.matches.append((entry["project_id"], finding))
        detector_runs = api.detector_runs(entry["project_id"], limit=20)
        if detector_runs:
            newest = max(r["ran_at"] for r in detector_runs)
            scan.last_run = max(scan.last_run, newest) if scan.last_run else newest
    return scan


def collect_row(api: PatApi, state: Any, scenario: Scenario) -> dict[str, Any]:
    projects = scoped(state, scenario.org).get("projects", {})
    run_info = state.get("runs", {}).get(scenario.id, {})
    own = set(run_info.get("trace_ids", []))
    scan = _scan(api, scenario, projects)
    matches = scan.matches
    evidence = [t for _, f in matches for t in f["evidence"]["trace_ids"]]
    run_keys = ("started_at", "ended_at", "calls", "failures", "error")
    row: dict[str, Any] = {
        "scenario": scenario.id,
        "detector": scenario.detector,
        "label": scenario.label,
        "mode": scenario.mode,
        "variant": scenario.variant,
        "projects": list(scenario.project_names),
        "fired": bool(matches),
        "fired_in": scan.fired_in,
        "finding_kinds_in_project": sorted(scan.kinds),
        "severity": max((f["severity"] for _, f in matches), default=None),
        "titles": [f["title"] for _, f in matches],
        "evidence_trace_ids": evidence,
        "matched_own_traces": any(t in own for t in evidence),
        "own_trace_count": len(own),
        "run": {k: run_info.get(k) for k in run_keys},
        "run_facts": run_info.get("facts", {}),
        "last_detector_run_at": scan.last_run,
        **_expectation_fields(api, scenario, matches),
    }
    row["expectation"] = None if scenario.expect is None else vars(scenario.expect)
    row["expectation_met"] = expectation_met(scenario.expect, row)
    return row


def collect(scenarios: list[Scenario]) -> Path:
    state = doctor_state()
    api = PatApi(state["pat"], HOST)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"doctor_{now_utc().strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    rows = []
    for scenario in scenarios:
        row = collect_row(api, state, scenario)
        rows.append(row)
        mark = "FIRED" if row["fired"] else "quiet"
        kinds = row["finding_kinds_in_project"]
        print(f"{mark:5s} {scenario.label} {scenario.id:48s} kinds={kinds}")
    with out.open("w") as fh:
        for row in rows:
            fh.write(json.dumps(row, default=str) + "\n")
    print(f"wrote {out}")
    return out

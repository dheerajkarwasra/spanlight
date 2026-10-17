"""Score a collected run of the Doctor accuracy harness.

    cd backend && uv run python -m accuracy.doctor.score [accuracy/results/doctor_<ts>.jsonl]

Per detector: TP (positive fired), FN (positive quiet), FP (negative fired), TN (negative
quiet), precision and recall with Wilson 95 % intervals (`app.insights.precision.wilson`, the
interval the API and the CLI publish). Then the cross-detector false positives (findings of other
kinds in any scenario project), anything found in the control projects, the positives that did
not fire, and whether each scenario with an expectation (a rate-limit kind, an incident citation)
met it. The latest results file is scored when none is given. Pure except for reading
the file, so `tests/accuracy/test_score.py` checks the arithmetic on hand-made rows.
"""

import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from accuracy.paths import RESULTS_DIR
from app.insights.precision import wilson

CONTROL = "control"


@dataclass
class Counts:
    tp: int = 0
    fn: int = 0
    fp: int = 0
    tn: int = 0
    unmatched_evidence: int = 0  # fired, but no evidence trace id the scenario created
    scenarios: list[str] = field(default_factory=list)

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None


def score(rows: list[dict[str, Any]]) -> dict[str, Counts]:
    """TP/FP/FN/TN per detector; control rows are not scored here (see `control_findings`)."""
    counts: dict[str, Counts] = defaultdict(Counts)
    for row in rows:
        if row["detector"] == CONTROL:
            continue
        c = counts[row["detector"]]
        c.scenarios.append(row["scenario"])
        fired, positive = bool(row["fired"]), row["label"] == "pos"
        if positive and fired:
            c.tp += 1
        elif positive:
            c.fn += 1
        elif fired:
            c.fp += 1
        else:
            c.tn += 1
        if fired and not row.get("matched_own_traces", False):
            c.unmatched_evidence += 1
    return dict(counts)


def cross_detector_findings(rows: list[dict[str, Any]]) -> list[tuple[str, str, list[str]]]:
    """`(scenario, detector, other kinds)` for every scenario project with findings of another
    kind: a finding nobody asked for is a false positive of that other detector."""
    out = []
    for row in rows:
        if row["detector"] == CONTROL:
            continue
        others = sorted(k for k in row["finding_kinds_in_project"] if k != row["detector"])
        if others:
            out.append((row["scenario"], row["detector"], others))
    return out


def control_findings(rows: list[dict[str, Any]]) -> list[tuple[str, list[str]]]:
    return [
        (row["scenario"], sorted(row["finding_kinds_in_project"]))
        for row in rows
        if row["detector"] == CONTROL and row["finding_kinds_in_project"]
    ]


def _fmt_rate(value: float | None, successes: int, total: int) -> str:
    interval = wilson(successes, total)
    if value is None or interval is None:
        return "n/a"
    return f"{value:.2f} ({successes}/{total}, 95 % {interval[0]:.2f}-{interval[1]:.2f})"


def _counts_line(name: str, c: Counts) -> str:
    precision = _fmt_rate(c.precision, c.tp, c.tp + c.fp)
    recall = _fmt_rate(c.recall, c.tp, c.tp + c.fn)
    return (
        f"| {name} | {c.tp} | {c.fn} | {c.fp} | {c.tn} | {precision} | {recall} | "
        f"{c.unmatched_evidence} |"
    )


def _detector_table(rows: list[dict[str, Any]]) -> list[str]:
    counts = score(rows)
    lines = [
        "| detector | TP | FN | FP | TN | precision | recall | fired w/o own evidence |",
        "|---|---|---|---|---|---|---|---|",
    ]
    totals = Counts()
    for detector in sorted(counts):
        c = counts[detector]
        totals.tp += c.tp
        totals.fn += c.fn
        totals.fp += c.fp
        totals.tn += c.tn
        totals.unmatched_evidence += c.unmatched_evidence
        lines.append(_counts_line(detector, c))
    lines.append(_counts_line("**all**", totals))
    return lines


def _cross_section(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "",
        "### Findings of other kinds in scenario projects (cross-detector false positives)",
    ]
    cross = cross_detector_findings(rows)
    if not cross:
        return [*lines, "none"]
    lines += ["| scenario | expected kind | other kinds found |", "|---|---|---|"]
    return lines + [f"| {s} | {d} | {', '.join(k)} |" for s, d, k in cross]


def _control_section(rows: list[dict[str, Any]]) -> list[str]:
    lines = ["", "### Findings in control projects (should be none)"]
    controls = control_findings(rows)
    if not controls:
        return [*lines, "none"]
    lines += ["| control | kinds found |", "|---|---|"]
    return lines + [f"| {s} | {', '.join(k)} |" for s, k in controls]


def _misses_section(rows: list[dict[str, Any]]) -> list[str]:
    misses = [r for r in rows if r["label"] == "pos" and not r["fired"]]
    if not misses:
        return []
    lines = ["", "### Positives that did not fire"]
    for r in misses:
        run = r.get("run") or {}
        lines.append(
            f"- {r['scenario']}: calls={run.get('calls')} failures={run.get('failures')} "
            f"error={run.get('error')} kinds={r['finding_kinds_in_project']}"
        )
    return lines


def _expected(expectation: dict[str, Any]) -> str:
    parts = []
    if expectation.get("rate_limit_kind"):
        parts.append(f"rate_limit_kind={expectation['rate_limit_kind']}")
    if expectation.get("incident_cited"):
        parts.append("incident_cited")
    return ", ".join(parts)


def _observed(row: dict[str, Any]) -> str:
    if "rate_limit_kinds" in row:
        return "kinds " + ", ".join(str(k) for k in row["rate_limit_kinds"])
    if "incident_citations" in row:
        titles = [str(c.get("title")) for c in row["incident_citations"]]
        return "cited " + "; ".join(titles)
    return "—"


def _expectations_section(rows: list[dict[str, Any]]) -> list[str]:
    expecting = [r for r in rows if r.get("expectation")]
    if not expecting:
        return []
    lines = ["", "### Scenario expectations", "| scenario | expected | result | observed |"]
    lines.append("|---|---|---|---|")
    results = {True: "met", False: "not met", None: "not fired"}
    for r in expecting:
        result = results[r.get("expectation_met")]
        lines.append(
            f"| {r['scenario']} | {_expected(r['expectation'])} | {result} | {_observed(r)} |"
        )
    return lines


def markdown(rows: list[dict[str, Any]]) -> str:
    return "\n".join(
        _detector_table(rows)
        + _cross_section(rows)
        + _control_section(rows)
        + _misses_section(rows)
        + _expectations_section(rows)
    )


def load(path: Path) -> list[dict[str, Any]]:
    with path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def latest() -> Path:
    files = sorted(RESULTS_DIR.glob("doctor_*.jsonl"))
    if not files:
        raise SystemExit(f"no results in {RESULTS_DIR}; run the runner with --collect first")
    return files[-1]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0]) if args else latest()
    rows = load(path)
    print(f"# Doctor accuracy: {path.name} ({len(rows)} rows)\n")
    print(markdown(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

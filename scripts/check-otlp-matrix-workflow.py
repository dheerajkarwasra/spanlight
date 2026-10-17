#!/usr/bin/env python3
"""Pin the weekly OTLP matrix workflow's triggers, permissions, action pins and issue reporting.

The workflow runs on a schedule against the latest exporter releases and reports a breakage by
rewriting one GitHub issue. It cannot be exercised on a pull request, so this reads
.github/workflows/otlp-matrix-weekly.yml and asserts its guarantees instead:

- it runs weekly (and by hand), with read-only repository access everywhere;
- only the `report` job may write issues, and it runs on failure;
- every `uses:` is a full commit SHA with a `# vX.Y.Z` comment (the rule of release.yml);
- no secret is referenced, so a fork or a changed recipe cannot exfiltrate one;
- the report keeps one issue under the `otlp-matrix` label and never comments or closes.

Usage: check-otlp-matrix-workflow.py [path/to/otlp-matrix-weekly.yml]
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKFLOW = ROOT / ".github/workflows/otlp-matrix-weekly.yml"
# One definition of the SHA-pin rule, shared with the release workflow check.
check_pins = runpy.run_path(str(ROOT / "scripts/check-release-workflow.py"))["check_pins"]

CRON = "0 6 * * 1"
LABEL = "otlp-matrix"
ISSUE_TITLE = "OTLP compatibility matrix: breakage"
REPORT_PERMISSIONS = {"issues": "write", "contents": "read"}
FORBIDDEN_ISSUE_COMMANDS = ("gh issue comment", "gh issue close", "gh pr ")


def check_triggers(wf: dict) -> list[str]:
    on = wf.get("on", wf.get(True))  # PyYAML reads the bare key `on` as boolean True
    if not isinstance(on, dict) or set(on) != {"schedule", "workflow_dispatch"}:
        return ["workflow must trigger on `schedule` and `workflow_dispatch` only"]
    errors = []
    if on["schedule"] != [{"cron": CRON}]:
        errors.append(f"schedule must be exactly one cron '{CRON}'")
    inputs = (on["workflow_dispatch"] or {}).get("inputs") or {}
    force = inputs.get("force_failure") or {}
    if force.get("type") != "boolean" or force.get("default") is not False:
        errors.append("workflow_dispatch needs input `force_failure` (boolean, default false)")
    return errors


def check_permissions(wf: dict, jobs: dict) -> list[str]:
    errors = []
    if wf.get("permissions") != {"contents": "read"}:
        errors.append("top-level permissions must be exactly {contents: read}")
    for name, job in jobs.items():
        perms = job.get("permissions")
        if name == "report":
            if perms != REPORT_PERMISSIONS:
                errors.append(f"job report: permissions must be exactly {REPORT_PERMISSIONS}")
        elif perms is not None and any(v != "read" for v in dict(perms).values()):
            errors.append(f"job {name}: must not be granted any write permission")
    return errors


def check_report(jobs: dict) -> list[str]:
    report = jobs.get("report")
    if not isinstance(report, dict):
        return ["no `report` job"]
    errors = []
    if "failure()" not in str(report.get("if", "")):
        errors.append("job report: `if` must include failure()")
    needs = report.get("needs") or []
    if set([needs] if isinstance(needs, str) else needs) != set(jobs) - {"report"}:
        errors.append("job report: must need every other job")
    script = "\n".join(str(step.get("run", "")) for step in report.get("steps", []))
    if LABEL not in script or ISSUE_TITLE not in script:
        errors.append(f"job report: must find or create '{ISSUE_TITLE}' labelled `{LABEL}`")
    errors += [
        f"job report: must not run `{command.strip()}`"
        for command in FORBIDDEN_ISSUE_COMMANDS
        if command in script
    ]
    return errors


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_WORKFLOW
    if not path.is_file():
        print(f"FAIL: {path} does not exist")
        return 1
    text = path.read_text()
    wf = yaml.safe_load(text)
    if not isinstance(wf, dict) or not isinstance(wf.get("jobs"), dict):
        print(f"FAIL: {path} is not a workflow with jobs")
        return 1
    jobs = wf["jobs"]
    errors = check_triggers(wf) + check_permissions(wf, jobs) + check_report(jobs)
    errors += check_pins(jobs, text)
    if "secrets." in text:
        errors.append("no secret may be referenced (`secrets.`); the report uses github.token")
    for error in errors:
        print(f"FAIL: {error}")
    if errors:
        return 1
    print("otlp matrix workflow: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
